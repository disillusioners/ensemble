# SONIC-MODULE-BRIEF — instances-anchor-be

Commission: instances-list-anchor — the instance list must always include the directly-linked
(selected) instance and its children. User report (Discord 2026-11-24): a direct link
`/projects/:projectId/instances/:instanceId` loads the left-side list with the MOST RECENT
instances; an older selected instance (and its subtree) is invisible.

## TASK
Add an optional `anchor` query param to `GET /api/instances` (the list handler,
`async def list_instances` @ routers/instances.py ~:484). When present and resolvable to a known
instance, the response's `instances` array ADDITIONALLY contains the anchor row and ALL its
DESCENDANTS. This is a presence guarantee: the FE list always contains the selection + subtree.

## FILES (exact — touch nothing else)
- `daemon/routers/instances.py` — new anchor Query param + merge logic in the list handler.
- `daemon/manager.py` — ONE new additive method `get_instance_anchor_family(anchor_id) -> list[dict]`
  (thin delegate to `self._lifecycle_service`, mirroring how `list_instances` delegates @ ~:16887).
  NO other change — `list_instances` signature stays UNTOUCHED.
- `daemon/services/instance_lifecycle.py` — ONE new additive method
  `get_instance_anchor_family(anchor_id) -> list[dict]` + a module-level cap constant.
  NO change to `list_instances`.
- `daemon/repositories/instance/repository.py` — ONE new additive batched read
  `list_by_ids(instance_ids) -> list[Instance]` (single SELECT ... WHERE instance_id IN (...) with
  deterministic ORDER BY; chunk very large id lists). NO change to existing methods.
- `tests/api/test_list_instances_anchor_param.py` — NEW (route boundary).
- `tests/api/test_list_instances_anchor_family.py` — NEW (family semantics end-to-end).

## FROZEN CONTRACT (the FE module codes against this; do not deviate)
1. Param: `anchor: str | None = Query(None, description=...)` on the list route.
2. Present + resolvable => response instances[] additionally contains anchor + descendants:
   - Family = anchor row first, then descendants in BFS order (level-by-level, deterministic).
   - Rows NOT already present are APPENDED after the normal rows; dedupe by instance_id against
     rows already in the response (normal page + its descendants).
   - Family rows BYPASS project_id / exclude_kb / search / source / order filters — the anchor is
     an explicit presence request (mirrors the existing precedent: descendants of in-scope roots
     are returned regardless of their own project_id).
   - Row shape IDENTICAL to normal rows: `inst.to_dict()` + `children` from the batched children
     map — the SAME shaping as the list path (instance_lifecycle.py ~:5219-5227). The anchor row
     and EVERY family row must carry a populated `children` list.
   - Envelope (`total`, `limit`, `offset`, `has_more`, `truncated`) keeps its EXISTING semantics —
     computed from the normal page ONLY. The anchor family NEVER touches total/has_more/truncated.
3. Cap: descendant expansion bounded by a named constant in instance_lifecycle.py (suggest
   `INSTANCE_LIST_ANCHOR_BFS_CAP = 500`, mirroring the LIVE_DESCENDANTS_BFS_CAP value in
   daemon/manager.py ~:14571). Anchor + descendants total <= cap. On cap hit: include the first
   `cap` family rows in BFS order, log ONE rate-limited WARN (mirror the MAX_DESCENDANTS_PER_PAGE
   WARN discipline at repository.py ~:1662-1690), and do NOT set the response `truncated` flag
   (that flag belongs to the normal page's BFS).
4. Degrade honestly: unknown anchor (lookup miss) / empty string / malformed uuid / anchor-family
   fetch exception => list returned UNCHANGED, HTTP 200, WARN logged. NEVER 422/500 from the
   anchor. Wrap the anchor-family fetch in try/except at the router (a DB failure that already
   failed the FIRST list call follows the pre-existing error path — not the anchor's business).
5. Event-loop discipline (v0.27.14 lane — HARD): NO sync DB on the loop. The anchor family fetch
   is a SECOND offloaded call in the router, EXACTLY like the prefs second-offload @ ~:625:
   `anchor_rows = await _offload(manager.get_instance_anchor_family, anchor)`.
   Merge the rows into `instances_data` BEFORE the prefs fetch (the `instance_ids` list @ ~:624)
   so the UI-prefs merge covers the appended rows too.
6. BFS implementation (lifecycle): fetch the anchor via `instance_repository.get(anchor_id)` —
   miss => return []. Then level-by-level: `list_child_ids_for_parents(current_level_ids)` (the
   batched IN-query @ repository.py:446 — the sanctioned pattern), collect next-level ids, fetch
   their rows via the new `list_by_ids` (ONE batched SELECT per level — NO per-row queries;
   per-row loops on this path are the exact defect class the v0.27.14 lane killed). Build the
   children map per level. Stop on empty level or cap.
7. `manager.list_instances` / `lifecycle.list_instances` / `repository.list` signatures UNTOUCHED.
   Reason: test stand-ins mock `list_instances` (tests/api/test_instance_ui_prefs_api.py-style
   `_ManagerStandin`) — a new kwarg there breaks every standin (fixture-rot). The anchor is a
   separate facade method, so no existing mock sees it.

## STORED EXPERIENCE (distilled — you cannot query the knowledge base)
- The list path is async/off-loop via `daemon/services/db_offload.py` (`offload(...)`; admission
  gate B4/D-43/D-67/D-79). A second route-local offload is the HOUSE PATTERN (the prefs read).
- `repository.list()` returns a 3-tuple (rows, total, truncated) — return-shape changes ripple to
  all callers; do not change it.
- F12 lesson: kwargs silently dropped at intermediate seams are invisible without end-to-end
  tests — your family pack must assert presence THROUGH the route, not the facade in isolation.
- 2026-09-09 WARN storm: descendant-cap WARNs from a high-frequency poller flooded logs (510/hr)
  — that is why the rate-limited WARN discipline exists. Mirror it for the anchor cap.
- `list_child_ids_permanent` (repository.py:423) has 4 live co-callers — do NOT touch it.
- `children` must come from permanent `instances.parent_id` rows (instance_hierarchy is transient).

## TESTS
`tests/api/test_list_instances_anchor_param.py` — mirror `tests/api/test_list_instances_source_param.py`
VERBATIM in strategy (httpx.AsyncClient + ASGITransport, in-memory SQLite, manager stand-in with
kwargs spy): (a) anchor accepted without 422; (b) forwarded verbatim to the new facade;
(c) absent anchor => facade not called, response byte-compatible with today; (d) envelope shape
unchanged.
`tests/api/test_list_instances_anchor_family.py` — real lifecycle + repo + SQLite rows (see
tests/unit/test_instance_tree_loading.py for the row-fixture pattern): (a) anchor OLDER than the
page window => anchor + descendants present in response; (b) family present with
include_descendants=false too; (c) unknown anchor => unchanged list; (d) anchor already in page
=> no duplicate rows; (e) cap respected — family bounded at the cap + WARN logged (caplog);
(f) children populated on family rows; (g) anchor family bypasses the project_id filter.
REGRESSION (must re-run, must stay green): tests/api/test_list_instances_source_param.py,
tests/api/test_instances_list_offload_prefs.py, tests/api/test_instances_list_response_bytes.py,
tests/api/test_instance_ui_prefs_api.py, tests/unit/test_instance_tree_loading.py (verify the
file exists at this base first; adapt if renamed).

## TEST COMMAND (CRITICAL — env hygiene)
cd /home/nea/ensemble-src-wt-instances-anchor-be && env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT ENSEMBLE_SELF_ENV=dev uv run pytest tests/api/test_list_instances_anchor_param.py tests/api/test_list_instances_anchor_family.py -q
Why: ambient ENSEMBLE_SELF_ENV=live SHADOWS the worktree .env for direct pytest (anomaly 3f23b845);
`env -u POSTGRES_*` glob is a silent NO-OP — scrub per-variable. First `uv run` syncs the worktree
.venv (slow, expected). ALWAYS run with CWD = the worktree (`import daemon` resolves CWD-first).

## WORKTREE
/home/nea/ensemble-src-wt-instances-anchor-be (absolute path)
branch `feature/instances-anchor-be`, base `bcebe77250ab6ae07212cfafc33b2a8e82608d64` (origin/latest)

## MERGE ORDER
Slot 1 — merges BEFORE module `instances-anchor-fe`.

## GIT DISCIPLINE
Commit ONLY the files above, pathspec-limited (`git add <file> ...`; NEVER `git add -A`, NEVER
`git stash`, NEVER push, NEVER merge — the dispatcher owns merges/push). 1-3 commits, clear
messages. Leave the worktree CLEAN (no tracked modifications) at report time.

## REPORT
Reply to sonic-developer with [sonic-module-report-v2], <=40 lines:
MODULE-REPORT: DONE|PARTIAL|FAILED
branch + tip sha (git rev-parse HEAD) / files touched / what changed / tests run (exact commands
+ pass-fail counts) / what is UNTESTED / known risks / bash-raises used / complexity
self-assessment.
