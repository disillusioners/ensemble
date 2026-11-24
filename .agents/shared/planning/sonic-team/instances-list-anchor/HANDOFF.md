# HANDOFF — instances-list-anchor (sonic-developer lane)

Commission: the instance list must always include the directly-linked (selected) instance and its
children. User report (Discord 2026-11-24, verbatim intent): "we need add param, include the
selected uuid, so selected instance and it child always available to the list."
Complexity verdict: **MEDIUM** (assessed at plan time; no handover raised, no override consumed).
Release: **MERGED + PUSHED + STAGED, NO PROMOTE** (tag `v0.31.51`; receipts appended post-stage below).

HANDOFF_PATH = .agents/shared/planning/sonic-team/instances-list-anchor/HANDOFF.md
ABSOLUTE: /home/nea/ensemble-src-wt-instances-list-anchor/.agents/shared/planning/sonic-team/instances-list-anchor/HANDOFF.md
Base = origin/latest @ bcebe77250ab6ae07212cfafc33b2a8e82608d64. Module branches:
`feature/instances-anchor-be` @ d628de9be (slot 1), `feature/instances-anchor-fe` @ 1ad33a170 (slot 2).
Diff range for the verification wave: **bcebe7725..v0.31.51** (tag at the integration tip).

## Files touched across modules

**Module `instances-anchor-be`** (branch `feature/instances-anchor-be` @ `d628de9be`; merged as `df4eff2cb`, slot 1):
- `daemon/routers/instances.py`
- `daemon/manager.py`
- `daemon/services/instance_lifecycle.py`
- `daemon/repositories/instance/repository.py`
- `tests/api/test_list_instances_anchor_param.py` (NEW, 198 lines)
- `tests/api/test_list_instances_anchor_family.py` (NEW, 252 lines)

**Module `instances-anchor-fe`** (branch `feature/instances-anchor-fe` @ `1ad33a170`; merged as `8edbd6335`, slot 2):
- `frontend/src/app/services/api.service.ts`
- `frontend/src/app/services/api.service.spec.ts`
- `frontend/src/app/services/instance.service.ts`
- `frontend/src/app/services/instance.service.spec.ts`

**Integration layer** (sonic-developer, on `feature/instances-list-anchor`):
- `.agents/shared/planning/sonic-team/instances-list-anchor/{HANDOFF,BRIEF-be,BRIEF-fe}.md`
- `CHANGELOG.md`, `pyproject.toml`, `daemon/__init__.py`, `tests/test_api.py`, `uv.lock` (version-bump commit, release machinery)

Union = 6 BE + 4 FE + 3 planning docs + 5 bump files. **Zero overlap** between the two modules' file
sets; both merges clean (`merge --no-ff`, no conflict, no bounce). `instances-view-state.service.ts`
was in the FE module's allowed set but needed NO edit (read-only anchor source).

**Commission-surface correction (verified fresh at base):** the commission's suggested FE surface
named `pages/instances/instances.component.*`; at this base the direct-link route
`/projects/:projectId/instances/:instanceId` renders an INERT STUB (`pages/instance-detail/`), the
visible left-side list is the chat page's `<app-instance-list>` (chat.html:8), and the selection's
SINGLE writer is the App root's `syncDetailVisibility` → `InstancesViewStateService`. The change is
therefore service-layer only; no route, component, or template edits.

## What changed

**BE — `anchor` presence param on `GET /api/instances`:**
- Router: new optional `anchor: str | None` Query param. When set, a SECOND offloaded call
  (`await _offload(manager.get_instance_anchor_family, anchor)` — the same route-local
  second-offload house pattern as the prefs read) fetches the anchor family; rows are merged into
  `instances_data` BEFORE the UI-prefs batch fetch (so pin/color/icon prefs cover the family rows),
  deduped by `instance_id`, and APPENDED after the normal rows (anchor row first, then BFS order).
  Family rows BYPASS `project_id`/`exclude_kb`/`search`/`source`/`order` (explicit presence
  request, mirroring the existing "descendants of in-scope roots regardless of own project_id"
  precedent). Envelope `total`/`limit`/`offset`/`has_more`/`truncated` keeps normal-page semantics
  ONLY. Unknown/malformed anchor or family-fetch failure ⇒ list unchanged, HTTP 200, WARN, never
  422/500 (try/except at the router).
- Manager: NEW facade `get_instance_anchor_family` (manager.py:16957, thin delegate).
  `manager.list_instances` signature UNTOUCHED — zero standin/fixture ripple.
- Lifecycle: NEW `get_instance_anchor_family` (instance_lifecycle.py:5237) — bounded BFS:
  `instance_repository.get(anchor_id)` (miss ⇒ []) then level-by-level
  `list_child_ids_for_parents` (the batched IN-query) + rows via the new batched `list_by_ids`;
  rows shaped `inst.to_dict()` + `children` (identical to the list path). Cap
  `INSTANCE_LIST_ANCHOR_BFS_CAP = 500` (:97, mirrors the manager's LIVE_DESCENDANTS_BFS_CAP value)
  with a rate-limited WARN (600 s window) on cap hit; the response `truncated` flag is NOT touched.
- Repository: NEW `list_by_ids` (repository.py:656) — one batched SELECT IN with deterministic
  ORDER BY (no N+1; per-row queries on this path are the v0.27.14 defect class).

**FE — anchor threading (service layer only):**
- `ApiService.listInstances` gains a trailing optional `anchor?: string`; the `anchor` query param
  is set only when truthy — absent ⇒ URL byte-identical (back-compat).
- `InstanceService` injects `InstancesViewStateService` and reads
  `detailVisible() ? (activeInstanceId() ?? null) : null` INSIDE `loadInstances()` at fetch time —
  the exact `searchQuery()` threading precedent: initial load, pagination (`loadMore`) and the 60 s
  poll tick all inherit it automatically. The 8 s badge poll / `listInstanceTree` stay
  anchor-free (never point a high-frequency poller at an expanded response). NO new writers to the
  view state; anchor rows render through the existing list template + parent_id tree builder.

**Integration seams (created by MY merge):** the modules integrate ONLY through the frozen
`anchor` contract (param name, appended-family semantics, degrade behavior) — both briefs pinned
it; FE was coded against the contract while BE landed in parallel. Gates re-run on the MERGED tree:
BE 44 passed; FE 5 suites / 236 passed; `tsc --noEmit -p tsconfig.app.json` exit 0.

## What is UNTESTED

- No live-daemon / real-PostgreSQL exercise of the anchor path — in-memory SQLite +
  httpx ASGITransport only (lane fence). No browser E2E deep-link visual check (FE module report).
- The router's anchor-fetch-EXCEPTION degrade leg (try/except) has no dedicated red test — covered
  defensively only (BE module report).
- Anchor-family performance envelope (>500-descendant families under the 60 s poll) not measured —
  bounded by the cap + rate-limited WARN by construction, not by benchmark.
- Whole-tree pytest sweep NOT run (scoped shard: 2 new packs + offload_prefs + response_bytes +
  tree_loading); whole-tree jest NOT run (5 scoped suites); `ng build`/AOT NOT run (no template
  change — same NA class as the instances-optd-fe-poll lane).
- FE `tsc -p tsconfig.spec.json` reports ~993 repo-wide pre-existing errors (mirror specs loosely
  typed by design; FE module report) — none in the new code; jest is the authoritative FE gate.
- **Pre-existing reds (NOT this lane's; proven base-identical):** `tests/api/test_list_instances_source_param.py`
  (6 failed) + `tests/api/test_instance_ui_prefs_api.py` (13 failed) fail in daemon-child shells
  whose ambient env arms AuthMiddleware (ENSEMBLE_AUTH_ISSUER / ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE
  etc.): those older fixtures never strip the middleware, while the newer tests (offload_prefs,
  response_bytes, AND both new anchor packs) disarm it at fixture setup. MEASURED at base bcebe7725
  in a throwaway detached worktree with the identical env-scrubbed invocation: the same 6+13 failure
  sets. In a clean (non-daemon-child) environment they pass. The BE module report's regression claim
  ("43 passed" incl. source) was corrected by this measured evidence; the module's own packs are green.

## Known risks

- Family rows bypass filters BY DESIGN (presence guarantee): a caller passing `project_id` plus a
  cross-project anchor WILL see the foreign rows; the FE never does (route-scoped anchor).
- `instances[]` can exceed `limit` by the family size — consistent with existing root-pagination
  (limit bounds roots; descendants were never bounded by it).
- The `truncated` flag does NOT reflect anchor-family capping — a capped family is shorter than the
  true subtree, disclosed ONLY via the rate-limited daemon WARN (the flag's documented meaning
  belongs to the normal page's BFS; deliberately not conflated).
- After the detail view closes, the FE's `mergeInstances` local-only preservation keeps
  previously-fetched anchor rows in the list until a non-append reload replaces the page —
  display-only, matches pre-existing local-only behavior.
- Contended-file exposure: `instance_lifecycle.py` + `daemon/manager.py` carry unmerged-commit
  pressure from OTHER lanes (langgraph-checkpoint-perf, measured 2026-11-17); this lane's changes
  are purely additive methods to minimize the cross-lane conflict surface — the leader sweep owns
  that reconciliation.
- Test-env asymmetry: a grader running the two pre-existing red files from a daemon-child shell
  sees 19 failures that do not exist in clean CI — reproduction documented in "How to verify".

## How to verify

On the MERGED tree (checkout the tag `v0.31.51` or the integration tip):
```
# BE gate (daemon-child shells MUST scrub per-variable + override self-env):
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT \
  ENSEMBLE_SELF_ENV=dev uv run pytest \
  tests/api/test_list_instances_anchor_param.py tests/api/test_list_instances_anchor_family.py \
  tests/api/test_instances_list_offload_prefs.py tests/api/test_instances_list_response_bytes.py \
  tests/unit/test_instance_tree_loading.py -q
# expect: 44 passed

# FE gate:
cd frontend && npm ci && npm test -- --runTestsByPath src/app/services/api.service.spec.ts \
  src/app/services/instance.service.spec.ts src/app/services/instances-view-state.service.spec.ts \
  src/app/components/instance-list/instance-list.component.spec.ts \
  src/app/components/instance-list/instance-list.rooms-btn.spec.ts
# expect: 5 suites, 236 passed

# Pre-existing red reproduction (env-armed auth; base-identical, NOT this lane):
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT \
  ENSEMBLE_SELF_ENV=dev uv run pytest tests/api/test_list_instances_source_param.py \
  tests/api/test_instance_ui_prefs_api.py -q   # 19 failed at BASE and at the tip, identically
```
Contract spot-checks (running daemon): `GET /api/instances?anchor=<old-instance-id>&limit=10` →
anchor + descendants appended (deduped); `GET /api/instances?anchor=<unknown-uuid>` → unchanged
list, HTTP 200; `GET /api/instances` (no anchor) → byte-compatible back-compat. FE manual: open
`/projects/<pid>/instances/<old-iid>` (deep link) → the left list shows the instance + its subtree.
Push receipt: `git ls-remote --tags origin refs/tags/v0.31.51` shows BOTH the annotated object and
its `^{}` peeled ref, at the same commit as `origin/latest`.

## Bash-raises used

none — every command ran under the 600 s default (warm-cache `uv sync` / `npm ci`, scoped shards,
the stage procedure runs under its own script).

## Complexity self-assessment

MEDIUM — two disjoint modules behind one frozen API contract, additive on both sides of the seam,
strong test precedents on both sides (source-param pack / optd offload+bytes packs on BE; jest
service specs on FE). Routing depth, not paths, drove the band. No handover was raised; no override
was consumed; the verdict is not softened.
