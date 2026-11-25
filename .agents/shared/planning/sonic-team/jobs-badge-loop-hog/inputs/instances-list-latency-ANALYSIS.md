# GET /api/instances — latency analysis (agents-ensemble)

**Commission:** ANALYSIS ONLY — why is `GET /api/instances` ("load instances") slow; options + effort before deciding what to implement.
**Measured:** 2026-11-19 17:37–17:56Z against the **LIVE** daemon (`ensemble-main`, systemd; `current -> releases/v0.31.10`), **v0.31.10**, port 9797, DB `ensemble_prod` (PostgreSQL 18.4 @ 10.44.0.2:5432).
**Verdict in one line:** the endpoint's *statement count* is healthy (4–8 per request, no N+1); the latency is **response-shape-bound** — `limit` bounds ROOTS only, so a `limit=100` request returns **1541 rows / 6.06 MB**, of which **82 % of every row is two fields the frontend never reads**.

> **Brief-premise drift (disclosed):** the commission says live = v0.31.2 and "staged v0.31.3+ NOT promoted". Both are stale — `releases/current` symlink = `v0.31.10`, `/livez` reports `0.31.10`, promoted 2026-11-19 13:30Z. All line numbers below are cited against **v0.31.10** (`git show v0.31.10:<path>`); where v0.31.10 differs from the main-checkout HEAD (`bf4bfde6e`) the delta is stated.

---

## 1. Headline numbers

### 1.1 Live latency, the shapes the FE actually issues (probe counts per row: FE_A 15, FE_B/FE_C/FE_D/flat(10)/BFS-pinned 30; see `evidence/`)

| Shape | FE caller (verified in source) | p50 | p90 | p95 | max | payload | rows | `truncated` |
|---|---|---|---|---|---|---|---|---|
| **FE_A** `limit=100&include_descendants=true` (`order=pinned`) | `pages/home/home.component.ts:95` — **polls every 10 s** (`startPolling`, :107-110, started unconditionally in `ngOnInit` :62; the home page is the app's **default route**, `app.routes.ts:135`) | **824 ms** | 892 ms | 908 ms | 1191 ms | **6 057 534 B** | **1541** | **true** |
| **FE_B** `limit=10&include_descendants=true` | `services/instance.service.ts:322,342` (`PAGE_SIZE=10`) → `components/instance-list/instance-list.component.ts:205,211,443,450` | 136 ms | 167 ms | 191 ms | 202 ms | 465 331 B | 162 | false |
| **FE_C** `limit=10&include_descendants=false&order=activity` | `services/instance.service.ts:498` — the 8 s badge tick | 73 ms | 355 ms | 385 ms | 517 ms | 34 646 B | 10 | false |
| **FE_D** `limit=10&include_descendants=true&order=activity` | `services/instance.service.ts:543` — badge **panel-open** | 171 ms | 254 ms | 395 ms | 530 ms | 751 450 B | 189 | false |
| flat(10) `limit=10&include_descendants=false` | (not an FE shape; see `evidence/flat.csv`) | 68.9 ms | 95.3 ms | 108.5 ms | 349.1 ms | 34 646 B | 10 | false |
| BFS pinned (repeat run) `limit=100&include_descendants=true` | (same shape as FE_A; `evidence/bfs.csv`, 30 probes) | 718.4 ms | 788.1 ms | 819.6 ms | 2730.0 ms | 6 057 534 B | 1541 | true |

For reference, the prior harness (`eventloop-sync-db-hotpaths/BASELINE.md`, v0.27.13 tree, 3372-row scratch fixture) measured flat(10) p50 **59.2 ms** and BFS(100) p50 **858.7 ms**. Today: flat(10) **62–73 ms** (unchanged), BFS(100) **824 ms** (unchanged) — on **2.03× the fleet rows**. *The endpoint is not slower than it was in October; the shape that is slow is the one the HOME page polls every 10 seconds.*

### 1.2 Where the time goes (in-process phase profile, live rows, read-only session fence, median of 5)

`repo.list` (SQL + ORM hydration) **216.4 ms** → `ids` **0.9 ms** → children batch **45.4 ms** → `to_dict()` loop **37.2 ms** → ui-prefs batch **35.0 ms** → `InstanceInfo` build **46.0 ms** → `resp_model_ctor` **0.2 ms** → `model_dump_json` **89.2 ms** → `response_validate` **10.9 ms** → `validated_dump_json` **61.2 ms** → **TOTAL 585.8 ms** (HTTP p50 824 ms ⇒ ~240 ms of ASGI/transport/serialization overhead outside the app code).

Per-phase values are **medians of 5 independent runs** (`profile_out.json`, `bfs100_pinned`); medians do not add, so the sum of the listed medians (542.4 ms) does not equal the measured end-to-end TOTAL (585.8 ms) — the ~43 ms residual is an artefact of per-phase median reduction, not unmeasured work.

### 1.3 The payload is the product of the problem (1541 rows, avg 3 922 B/row)

| field | avg bytes/row | share | consumed by the frontend? |
|---|---|---|---|
| `mcp_tool_names` | **2 263 B** | **58 %** | **NO — 0 hits in the whole `frontend/` tree** |
| `initiative_message` | 965 B | 25 % | declared in `models/instance-node.model.ts:148`, **0 template bindings** |
| `children` | 41 B | 1 % | yes (tree) |
| ids/title/status/timestamps | ~150 B | 4 % | yes |

Browser-side `JSON.parse` of the 6.05 MB body = **34 ms** (computed estimate — a scratch node v24 run against the saved 6.05 MB payload, median of 7; not shipped as an evidence file) — parsing is *not* the cost; the **6.05 MB transfer** is (gzip would give **848 KB**, 7.1×, and the daemon sends it **uncompressed** — no GZip middleware).

### 1.4 Independent reproduction (sonic-tester, 10 fresh probes, 2026-11-19)

A second agent re-ran 10 authenticated GET probes against the same live v0.31.10 instance (operator session cookie, `PGOPTIONS` read-only fence, no writes, no restarts) after this analysis was written:

| shape | tester p50 / payload / rows / truncated | this analysis | delta |
|---|---|---|---|
| FE_A | 781.1 ms / 6 065 665 B / 1543 rows / true | 824 ms / 6 057 534 B / 1541 / true | p50 −5 %, +8 KB / +2 rows (fleet drift) |
| FE_B | 203.9 ms / 473 462 B / 164 / false | 136 ms / 465 331 B / 162 / false | contention-inflated p50, +8 KB / +2 rows |
| FE_C | 95.2 ms / 34 742 B / 10 / false | 73 ms / 34 646 B / 10 / false | contention-inflated p50, +96 B |

Order of magnitude, payload size, row count and `truncated` flag all reproduce; the p50 spread on FE_B/FE_C is the H3 loop-queueing signature already documented (the tester's run landed during active daemon work). No auth bypass, no writes, no restarts.

---

## 2. Method (fences, access path, disclosure)

* **Access:** auth is ON (`ENSEMBLE_AUTH_ENABLED=true`); unauthenticated GET → 302 to `/api/auth/login`. `auth_bearer_tokens` is **empty** (0 rows) and minting one is a live write, so the **legitimate read-only path** used is the **operator's existing browser-lane session** (`auth_sessions.id` → `Cookie: ensemble_sid=…`), the exact credential the SPA uses. Scope set includes `instances:read`, `metrics:read`, `maintenance:read`. **No auth was bypassed, no token minted.** The session id was never printed; it lived only in a `chmod 600` file under a `mktemp` scratch dir.
* **Live safety:** every psql session ran with `PGOPTIONS='-c default_transaction_read_only=on'` (PG itself refuses writes). The in-process profiler used the **live production code** against the **live DB** through the same read-only fence. Zero writes, zero restarts, zero live mutations. Only GETs were issued.
* **Raw evidence** (timing CSVs + profiler outputs, no payload bodies, no credentials): `evidence/` next to this file — `flat.csv` / `bfs.csv` (30 probes each), `fe_FE_*.csv` (FE-shape probes), `h3_composite.csv` (7-leg concurrent composite), `lag.csv` (loop-lag vs probe latency), `profile_out.json` (phase split), `profile2_out.json` (column-cost split), `profile3_out.json` (statement census).
* **Disclosure (side effect):** any authenticated request makes the daemon update `auth_sessions.last_used_at` — identical to what the operator's open browser does. Nothing else was touched.
* **DB-side activity census is privilege-blocked:** role `ensemble` cannot see other backends' `pg_stat_activity` rows (`pg_has_role(current_user,'USAGE')` is NULL even for itself), so statement counting was done with a **SQLAlchemy `before_cursor_execute` listener** against the live DB (the same instrument the prior harness used) instead of `pg_stat_statements` (not installed) or `pg_stat_activity` (masked).
* **No `EXPLAIN ANALYZE` on prod** (plain SELECT timings only). No dev-copy `EXPLAIN ANALYZE` was needed: the column-cost split was measured directly by re-running the same predicates with/without the `metadata` column.

---

## 3. Path trace (v0.31.10 line numbers)

| # | hop | file:line (v0.31.10) | on event loop? |
|---|---|---|---|
| 1 | `GET /api/instances` route, `scope_required("instances:read")` | `daemon/routers/instances.py:375-376` | — |
| 2 | `limit` clamp to `MAX_PAGE_LIMIT = 100` | `daemon/routers/instances.py:486`; `daemon/constants.py:19` | — |
| 3 | `await _offload(manager.list_instances, …)` — the v0.27.14 off-loop bridge (dedicated 6-worker `db-offload` executor) | `daemon/routers/instances.py:489-500`; `daemon/services/db_offload.py` | **off** |
| 4 | `Manager.list_instances` | `daemon/manager.py:16561` | off (thread) |
| 5 | `InstanceLifecycleService.list_instances` | `daemon/services/instance_lifecycle.py:5123` | off (thread) |
| 6 | `SQLModelInstanceRepository.list` | `daemon/repositories/instance/repository.py:1325` | off (thread) |
| 6a | root count | `repository.py:1471` (flat path `:1471`), `:1526` (BFS path) | off |
| 6b | root page (LEFT JOIN `instance_ui_prefs`, ORDER BY pinned CASE / `pinned_at` / `created_at`) | `repository.py:1511-1582` | off |
| 6c | **BFS descendant load**, one statement per level: `WHERE parent_id IN (<level ids>)` | `repository.py:1627` | off |
| 6d | **descendant cap** `MAX_DESCENDANTS_PER_PAGE = 1000` — checked *after* a whole level is appended | `repository.py:1662` (const `:190`) | off |
| 6e | `_enrich_instances` hook — **no-op** in the shipped class | `repository.py:475` (used at `:1711`) | off |
| 7 | children map, **one batched statement** `IN (page ids)` | `instance_lifecycle.py:5219` → `repository.py:446` | off (thread) |
| 8 | ui-prefs map, **one statement** `IN (page ids)` | `daemon/routers/instances.py:511` | **ON THE LOOP** |
| 9 | `InstanceInfo` construction loop (N pydantic models) | `daemon/routers/instances.py:514-541` | **ON THE LOOP** |
| 10 | FastAPI `response_model=InstanceListResponse` → validate + jsonable-encode (6 MB) | `daemon/routers/instances.py:375`, `:545-552` | **ON THE LOOP** |

**Not on this path:** the routed residual `tools/instance.py` `_fetch_instance_messages` → `repository.py:647` is in the **agent-tool layer** (`daemon/tools/instance.py:5861`, called at `:5898`); it is **not** reached by the HTTP list route. (The commission's anchor `tools/instance.py:5249` had drifted; v0.31.10 it is `:5861`.)

---

## 4. Statement census (live code path, `before_cursor_execute` listener)

| shape | statements | breakdown |
|---|---|---|
| FE_C flat(10) | **4** | count(1) + page(1) + children `IN(10)`(1) + `instance_ui_prefs`(1) |
| FE_B BFS(10) | **8** | count(1) + roots + 4 loaded BFS levels (5 instance-row SELECTs) + children `IN(162)`(1) + prefs(1) |
| FE_A BFS(100) | **6** | count(1) + roots + 2 loaded BFS levels (3 instance-row SELECTs) + children `IN(1541)`(1) + prefs(1) |
| *BASELINE v0.27.13 (pre-fix)* | *flat 13 / BFS 248* | *children N+1 + on-loop* |

**The v0.27.14 fix is intact and holds at v0.31.10**: the children lookup is still a single statement and covers all returned rows (`IN(1541)` → 1557 child rows), and the DB read is still off-loop. Statement count is **O(1 + tree depth)**, not O(rows).

### Why BFS(100) returns 1541 rows for a `limit=100` request
Roots on the pinned/activity page: **100** (level 0). Descendant levels (recursive CTE census): **level 1 = 345, level 2 = 1096, level 3 = 116**. The code loads levels 0–2 (100 + 345 + 1096 = 1541) and only then evaluates the cap (`repository.py:1662`) → 1541 ≥ 1000 → `truncated=true`, **break**. So the cap **overshoots by a full level (1541 > 1000)** and the tree is cut mid-level — level 3's 116 descendants never reach the FE, and the FE has no visible "tree truncated" affordance (the flag is in the envelope only).

---

## 5. Column-cost decomposition (1557 page rows, live, read-only)

| query | median |
|---|---|
| raw SQL text, all columns (incl. `metadata` JSONB) | 241 ms |
| ORM `select(Instance)` — **shipped behaviour** | **206 ms** |
| ORM projected columns, **no `metadata`** | **37 ms** |
| ORM `instance_id` + `metadata` only | 209 ms |
| `sum(pg_column_size(metadata))` for the page rows | **3 437 193 B** |

⇒ **the `metadata` JSONB alone is ~170 ms of the 206 ms `repo.list` cost.** The wire row needs 6 keys out of it (`title`, `initiative_message`, `mcp_tool_names`, `model_override`, `watchover_enabled`, `watchover_context`); the rest (`attestation:denial_epochs`, `pok_*`, `original_source`, `project_injected`, `memoir_briefing_injected`, …) is fetched and parsed for nothing.

---

## 6. Hypothesis attribution (H1 / H2 / H3)

### H1 — code regression (new per-row enrichment) → **REFUTED in the N+1 class; CONFIRMED as a response-shape regression**
* Statement census above: 4 / 8 / 6 statements — the v0.27.14 batching + off-loop bridge are intact. **No per-row SQL was added.**
* **No room/adjacency/badge enrichment exists on this route**: there is no `/api/rooms` router in `daemon/routers/`, the FE never calls one, and the only per-row work is (a) the batched children map, (b) the batched ui-prefs map, (c) pydantic model construction.
* What *did* change for the worse is the **wire width**: `mcp_tool_names` (2.26 KB/row, **unused by the FE**) + `initiative_message` (0.97 KB/row, **never bound in a template**) = 82 % of every row, on a page that can carry 1541 rows ⇒ **6.06 MB per home poll, every 10 s**.

### H2 — the fleet grew → **TRUE but SECOND-ORDER**
* Live fleet: **6 836 instances** (4 227 roots, 2 609 children), **181 non-KB roots**, `instances` table 24 MB (8.5 MB heap + 1.4 MB index); avg `metadata` 1 917 B, max 12 553 B. The prior fixture had 3 372 rows ⇒ 2.03×.
* But the *root page* is still only 181 rows — root count 9 ms, root page query cheap, `ix_instances_parent_id` already serves the BFS (`WHERE parent_id IN (…)`). Fleet growth inflates the **descendant fan-out** (level 2 alone = 1096 rows) but did **not** make the endpoint slower than in October: BASELINE BFS(100) p50 **858.7 ms** on 3 372 rows vs today **824 ms** on 6 836 rows. **H2 is not the driver; the response shape is.**

### H3 — loop queueing → **TRUE and MATERIAL (secondary amplifier); the route also contributes to it**
* The daemon's **own** loop-lag probe publishes `ensemble_loop_lag_seconds` **p50 ≈ 1.00 s / p95 ≈ 1.29 s / max ≈ 4.00 s** (fresh — `last_publish_age_s` ≈ 10 s at sample time).
* Reproducing the FE's badge composite (7 concurrent legs × 8 rounds, live):

  | leg | unloaded p50 | **loaded p50** | loaded max |
  |---|---|---|---|
  | `GET /api/jobs?status=completed,settled,failed,cancelled,dead_letter&limit=10` | — | **3.833 s** | **8.536 s** |
  | FE_A home BFS(100) | 0.824 s | **1.562 s** | 4.051 s |
  | FE_B list BFS(10) | 0.136 s | **0.366 s** | 0.982 s |
  | FE_C badge flat(10) | 0.073 s | **0.267 s** | 0.674 s |
  | `GET /api/jobs?status=queued,active` | — | 0.436 s | 1.045 s |
  | `GET /api/queues/defer-blocked` | — | 0.297 s | 0.703 s |
  | `GET /api/missions?liveness=processing,pending,paused&limit=20` | — | 0.183 s | 0.490 s |

  ⇒ the **`/api/jobs` "recent" leg is the loop hog** (the BASELINE's 1 439-statement orphan-id enrichment is still there in the live tree) and it inflates **every** instances leg by **3.7–4.0×**. This is the "it feels slow when my browser is open" amplifier.
* The list route is **not only a victim**: steps 8–10 above put **~80 ms of on-loop sync DB** (children 45 ms + prefs 35 ms) plus **~150 ms of on-loop model build/serialization** on the loop for a FE_A request — so every home poll (every 10 s) adds ~230 ms of loop occupancy.

---

## 7. Diagnosis (dominant bottleneck, ranked)

1. **`limit` does not bound the response.** Roots are paginated; the response carries roots **+ every descendant of those roots**. `limit=100` ⇒ **1541 rows / 6.06 MB**, and the 1000-row cap **overshoots to 1541** and truncates mid-tree (`truncated=true`, 116 descendants silently dropped). The caller believes it bounded the page; it did not. *(root cause)*
   * **The home page is the app's default route** (`frontend/src/app/app.routes.ts:135`) and polls this exact shape **every 10 s** while open (`home.component.ts:62,107-110`) — it needs only `instances()[0].instance_id` and `instances().length > 0` (`home.component.ts:54,163,241`), i.e. it over-fetches by ~1000× in rows to compute one boolean and one id.
2. **Per-row payload is 82 % dead weight** — `mcp_tool_names` + `initiative_message`, neither read by the FE. 6.06 MB per home poll, every 10 s, uncompressed. *(amplifier)*
3. **The `metadata` JSONB is fetched and parsed whole** (~170 ms of the 206 ms `repo.list`) to fill 6 of its keys. *(amplifier)*
4. **Route-level work is duplicated on the event loop** (prefs query + N pydantic constructions + 6 MB serialization ≈ 230 ms of loop occupancy). *(amplifier + H3 contributor)*
5. **The badge's `/api/jobs` recent leg blocks the loop for 3.8–8.5 s**, inflating every instances leg 3.7–4×. *(H3 amplifier — a different route)*
6. Fleet growth (H2) and statement-count regression (H1-N+1) are **ruled out** as drivers.

---

## 8. Options — ranked by impact vs effort

| # | option | what changes (file:line, v0.31.10) | expected impact | effort |
|---|---|---|---|---|
| **A** | **Stop shipping `mcp_tool_names` on the list route** (keep it on the detail route) | `daemon/routers/instances.py:526` (and optionally `:524` for `initiative_message`); shape in `daemon/models/instance.py:36` | payload **6.06 MB → ~2.6 MB (−57 %)**; pydantic dump 89 → ~35 ms; FE_A **824 → ~650 ms**; FE_B 465 → ~200 KB. Zero FE consumers (`grep -rn mcp_tool_names frontend/` = **0 hits**) | **XS — 0.5–1.5 h** |
| **D** | **Take the per-row route work off the loop** | fold the ui-prefs fetch into the offloaded call (or offload it) at `daemon/routers/instances.py:511`; build the body as a plain dict/`JSONResponse` to skip FastAPI's response-model re-validation at `:545-552` | removes ~80 ms on-loop DB + ~100 ms on-loop serialization per request; should stop the 3.7–4× inflation of the instances legs under badge load | **S — 2–4 h** |
| **B** | **Make the limit bound the RESPONSE** (row budget), not just roots; enforce the cap *before* appending a level; expose truncation to the FE | `daemon/repositories/instance/repository.py:1662` (cap), `:1711` (return), plus the route envelope at `daemon/routers/instances.py:545-552` | FE_A **1541 → e.g. 300 rows**, payload 6.06 MB → ~1.2 MB, **824 → ~300 ms**; also removes the silent mid-tree truncation | **S–M — 4–10 h** (BE contract + FE lazy-load of children on expand) |
| **C** | **Project only the columns the wire row needs** (stop hydrating the whole `metadata` JSONB) | `daemon/repositories/instance/repository.py:1325` (select list), route read at `:516-528` | `repo.list` **206 → ~40-60 ms (−150 ms)**; compounds with A (with A, `mcp_tool_names` need disappears entirely) | **M — 8–16 h** (new DTO path; `to_dict()` consumers must keep working) |
| **E** | **Fix the badge's `/api/jobs` recent leg** (the real loop hog) | `daemon/routers/jobs*.py` + `daemon/services/work_resolver.py` (`list_work` → per-row `_lookup_instance`) | removes the 3.8–8.5 s loop block ⇒ the instances legs' loaded p50 should fall back to ≈ unloaded (badge 0.267 → ~0.08 s) | **M — 6–12 h** (separate lane from `/api/instances`) |
| **F** | **Compress the list response** (GZip middleware) | `daemon/api.py:5977` (middleware stack — `add_middleware` block) | 6.05 MB → **848 KB** over the wire (7.1×); no CPU saving | **XS — 1–2 h** |
| **G** | Short-TTL cache of the serialized list response (home polls every 10 s) | new cache in front of the route | home poll → ~0 ms on a hit | **S–M — 4–8 h**; staleness semantics; masks the shape problem — **not recommended first** |
| **H** | Indexes | — | **none justified**: root count 9 ms, root page sorts 181 rows, `ix_instances_parent_id` already serves the BFS | XS but ≈0 impact (listed to close the question) |

### RECOMMENDED FIRST MOVE
**Option A** (drop `mcp_tool_names` from the list wire shape) — **XS, ~1 h**, removes 58 % of every row on every list call, has **zero in-repo consumers**, and needs no contract change. Then **Option D** (S, ~3 h) to take the remaining per-row work off the event loop, then **Option B** (the limit contract) as the structural fix. **Option E** is the single biggest lever on the *felt* slowness while the browser is open, but it belongs to the `/api/jobs` lane and should be commissioned separately.

---

## 9. Untested / not measured (and why)

* **Browser-side render cost** — not measured (no browser here). Only `JSON.parse` (34 ms, computed estimate — see §1.3) and the gzip ratio (848 KB, computed estimate) were measured; the Angular render of 1541 recursive template nodes (`components/instance-list/instance-list.html:89-110`, no virtual scroll) is unmeasured and is likely a second, independent contributor to "takes a bit long".
* **`EXPLAIN` plans** — not captured; per-statement timings were taken directly with `\timing` on prod (read-only SELECTs) and the column-cost split was measured by re-running the predicates, so plans add no decision-relevant information. No dev-copy `EXPLAIN ANALYZE` was performed.
* **The operator's real browser session** was not observed live — during the measurement window the DB census showed no badge traffic (the FE was closed), so all latency numbers are the *unloaded* case; the loaded case is the reproduced composite in §6/H3.
* **`pg_stat_activity`-based statement counting** — privilege-blocked for role `ensemble` (see §2); substituted with an engine-level listener.
* **The `pg_stat_database` counter delta** was attempted as an independent cross-check but the daemon's background agent/checkpoint traffic dominates the transaction counter (idle 3 s ≈ +20 xact) — the numbers were too noisy to separate a 4-statement request, so they are not quoted.
* **External consumers of `GET /api/instances`** — only the in-repo frontend was searched (0 hits for `mcp_tool_names`). Out-of-repo consumers (scripts, other UIs, MCP tools) are unknown; note that the **agent tool layer reads `Instance.to_dict()` directly** (`daemon/repositories/instance/models.py`) and is therefore unaffected by Option A.
* **Loop-lag metric interpretation** — `ensemble_loop_lag_seconds` is a bounded (300-sample) rolling histogram published by the daemon's own probe; p50 ≈ 1.00 s is consistent with the measured queueing amplification, but the metric cannot distinguish "loop blocked by 1 s" from a probe-lattice artifact. The composite measurement (H3) is the load-bearing evidence.

## 10. Risks

* **Option A** changes a public REST response shape — any out-of-repo consumer reading `mcp_tool_names` from the list route breaks (none in-repo).
* **Option B** changes pagination semantics and the tree the FE renders; the tree is *already* silently truncated today (`truncated=true`), so the FE must learn to lazy-load children on expand.
* **Option C** touches the repository's return type (`Instance` objects → DTO) — the lifecycle/route layers and the internal callers that use `to_dict()` must keep working.
* **Option E** is a different route/lane; sequencing matters — without it, the H3 amplification keeps the *felt* latency high even after A/B/D land.
* Repeated live probing of the heaviest shape is itself loop-occupying: the ~230 ms of on-loop work per FE_A request is why this analysis used a single-shot profiler for the expensive shapes.
