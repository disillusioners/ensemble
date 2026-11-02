# Phase 3: Query API Routes + Manifest Cages + Lifespan Wiring

Status: pending · Tier **2 — MECHANICAL ESCALATION — CONFIRMED** (the diff touches `daemon/api.py` ⇒ the
gate doctrine's mechanical list escalates this leg to whole-tree dual-side, ≤90 min advisory; the
architect confirmed the escalation against the canon — `ensure.md:19`)
**FOLDED 2026-11-02:** architect v2 amendments folded — ids cited inline as `(AM-n)`. **AM-22 execution
provisions** (hang-class clause · 137-min budget precedent · expected-skip census · PROJECTION node counts)
are folded into this leg's whole-tree section below.
Feeds: architect → plan council → fresh approver → implementation

## Objective

Open the **one door**: three JSON routes under the **existing** `/api/metrics` router family, backed by the
Phase-2 provider, with the manifest cages moved in the same commit as the code, and with the lifespan
wiring (provider construction + sampler start/stop) placed exactly where the house lifespan discipline
requires.

Cage arithmetic (verified against the tree, **AM-21 corrected**):

- `tests/integration/test_router_class_manifest.py:385` asserts `len(api_families) == 33` — **unchanged**
  (the query routes extend the existing family, so they add **no** family).
- `tests/unit/test_router_scope_manifest.py::test_declared_route_count_pinned` (:333-396) asserts
  `len(declared) == 108` — the **DECLARED-ROUTE CENSUS** of scope-decorated routes. The three new
  scope-decorated query routes take it to **111**. (The "metrics_router (34 unconditional)" string at
  :298-300 is a **docstring narrative**, not the pin — the earlier "route-count pin 34→37" target was
  **vacuous**: P3-M7 would have passed while the real pin went red.)
- The **mount census stays 37** (static).

## Tasks

| # | Task | Depends On | Acceptance | Traceability |
|---|---|---|---|---|
| 3.1 | Add the three routes to `daemon/routers/metrics.py` — `GET /api/metrics/families`, `GET /api/metrics/snapshot`, `GET /api/metrics/series` — on the **existing** router (prefix `/metrics`, `:59`), each with the family's pinned scope `maintenance:read` (`:70`, `:113`) and the **same** fail-closed `_require_auth_on` seam (`:91-107`) running **before any registry read**. The handlers are **thin adapters** over the provider, and they **BUILD the response body OFF the event loop** (AM-12: `await db_offload.offload(...)`, the `/api/work` precedent, `daemon/routers/work.py:181-193`). **AM-12 claims BUILD-OFF-LOOP ONLY** — `db_offload.offload` returns a Python object (`db_offload.py:386-392`) and FastAPI still **encodes it on the loop**; the residual encode is therefore **bounded and pinned**: `max_points` ≤ **240** × `?names=` ≤ 50 ⇒ **≤ 12 000 points ≈ 3.6 ms** on-loop (re-derived for the raised default — approver B1 + leader ruling). **No pre-encoded-`Response` precedent** is introduced. | Phase 2 | All three routes exist under `/api/metrics`; no new router module; the scope dependency is the family's pinned one; the response body is produced off the loop | FR-A1, FR-A2, FR-A3, FR-A9, FR-A6 · D4 · **AM-12** · AC-A.1, AC-A.2, AC-A.3, AC-A.7 · seam S18 (`daemon/routers/metrics.py:59/:70/:91-107/:113`) |
| 3.2 | Implement the **error mapping** for the routes: `404` unknown family (typed body) · `400` invalid label filter · `503` registry unavailable · `503 AUTH_NOT_CONFIGURED` (auth off) · `403` scope miss · `401` (auth on, no session) — never a 500 for an expected condition. | 3.1 | Each condition maps to its typed status; a malformed filter returns 400 (not 500/200-empty) | FR-A10 · D4 · AC-A.7 |
| 3.3 | Implement the **pagination + bounds** on `/series` (`?series=` **label-aware selector**, AM-1 — RFC-3986-escaped, alongside the name-level `?names=` ≤50 which returns every label variant; `?window_s=` clamped to the buffer window; `?max_points=` server-side **bucket-max** stride, **default 240**) and on `/snapshot` (`?family=`, `?name_prefix=`), with the **AM-6 cursor** (`next_cursor` opaque, pinning the evaluation instant + position; invalid/expired ⇒ `400 invalid_cursor`; a byte-ceiling response is bounded + carries a cursor, never a silent truncation) and a stated byte ceiling. | 3.1, Phase 2 (2.9) | An over-large request returns a bounded body + a token; the byte ceiling is asserted; an expired cursor returns 400 `invalid_cursor`; a label-aware `?series=` selects exactly one labeled series | FR-A11 · D4 · **AM-1, AM-6** · AC-A.8 |
| 3.4 | Wire the **lifespan** in `daemon/api.py`: construct the provider, store it on `app.state.metrics_query` (beside `app.state.metrics_listener`), and start the sampler **LAST of the metrics trio** — after the census block (`api.py:2467-2483`), and **the listener starts FIRST and must NOT be reordered** (AM-13 corrects the earlier un-implementable "after census, before listener" instruction); on shutdown, the sampler stops **FIRST**, before the listener stop (`api.py:3700-3709`) with the detached-join discipline (listener `stop()` precedent `:551-593`). A bind/start failure degrades (log + continue), never aborts the boot. **The query provider is NOT a `/readyz` component** (AM-24): the soak-gate grace discipline parses the readiness composite, and the provider must not be added to it. | Phase 2 | The wiring test asserts the exact start order (listener → census → sampler) and the reverse stop order (sampler → listener); a bind/start failure degrades; no readiness component references the provider | FR-D1, FR-A6 · D1, D3 · **AM-13, AM-24** · research §1c · seams S1, S18 |
| 3.5 | **Bump the cages in the same commit as the routes (AM-21)**: the **declared-route census 108→111** (`tests/unit/test_router_scope_manifest.py::test_declared_route_count_pinned`, :333-396, `assert len(declared) == 108`) plus the narrative comment recording why; the **mount census stays 37** (static); `tests/integration/test_router_class_manifest.py` stays at **33** (no change — assert it is untouched). | 3.1 | Both manifests green; the class-manifest file shows a zero-line diff; the declared-route census reads 111 | FR-F4 · D4 · **AM-21** · AC-F.2 · plan-overview flag D-9 |
| 3.6 | Build the **route pack** (`metrics_query_routes`): route existence (real `create_app`, `test_metrics_route.py` pattern), 200-with-scope, 503 auth-off, 403 scope miss, 401 auth-on-no-session, envelope shape per Phase 1 (including the `/families` **`buckets`** array — approver B3), `restart_epoch` present, availability reasons surfaced, **and the NFR-1 p95 measurement** (snapshot ≤ 250 ms p95, 60-min trend ≤ 1 s p95 — the approver's executability note): the pack measures the two latencies and reports them, so the NFR is measured rather than asserted. | 3.1–3.4 | The pack is green and the auth matrix matches `/api/metrics` exactly | FR-A9, FR-A4, FR-D4 · D4 · AC-A.7, AC-A.4 |
| 3.7 | Build the **corrected switch-invariance pin** (AM-18): with `ENSEMBLE_METRICS_ENABLED` ON and OFF — (i) **auth behaviour byte-identical** (status codes, error vocabulary, scope decision, fail-closed seam); (ii) the **registry-sourced series set identical** for every family whose availability reason is not switch-derived; the **only permitted** body differences are (i) F6 `availability` → `census_gated`, (ii) `activation.census_enabled` → false, (iii) `last_publish_age_s` growth — all by design and excluded from the claim. Plus a grep pin asserting the query package and the routes contain **no** `ENSEMBLE_METRICS_ENABLED` / `metrics_enabled` consultation (`census_gated` comes from the public `CensusSampler.gated` property (created by phase-2 task 2.4a — a read-through of the existing `_gate()`, **no third check site**). The check-site census over `daemon/` stays at exactly **2**. | 3.1, 3.4 | The corrected invariance claim holds; the grep pin is red if a consultation site appears; the census count is 2 | FR-A7, FR-D3, FR-D4, FR-D5 · **D-11 (AM-17)** · D1(b) · **AM-18** · R4 · AC-A.6, AC-D.1 |
| 3.8 | Prove the **frozen contracts are structurally stable (AM-16)**: the `/api/metrics` Prometheus-text body and the `:9798` listener body are **structurally identical** to the base artifact for the same registry state — same series set, TYPE lines, label vocabularies and HELP structure — **except the enumerated 3-carrier value-delta allowlist**: (1) the `# frozen vocabulary version: N` footer, (2) the `ensemble_metrics_vocabulary_version` gauge value, (3) the `# HELP ensemble_checkpoint_op_seconds …` text. **Any delta outside that allowlist is a gate red.** The listener stays OFF-gated, loopback-only, never-503. | 3.1 | Body diff against the base artifact = zero deltas outside the allowlist (re-scoped AC-A.5) | FR-A6, FR-F3 · D4 · **AM-16** · AC-A.5 |

**Not in this phase:** family registration (Phase 4), FE work (Phase 5), any producer-module edit, any
config default change.

## Gate legs

| Leg | Tier | Scope | D1 (lane-owned, HEAD-only) | D2 (dual-side, comparable) | Expected D1 / D2 nodes |
|---|---|---|---|---|---|
| **P3-G1** | **2** | `daemon/routers/metrics.py` + `daemon/api.py` + `daemon/services/metrics_query/**` ⇒ **mechanical** escalation (whole-tree dual-side, ≤90 min advisory) | `metrics_query_routes` · `metrics_query_auth_matrix` · `metrics_query_pagination` · `metrics_query_switch_invariance` · **`metrics_query_api_wiring`** (the D1 home of the lifespan wiring test that P3-M5/M6/M10 mutate — approver N-b: the wiring test previously sat only in the D2 composition, so a D1 mutation had no pack to ride) | **REGISTERED PACK `metrics_p3_dual_side`** — composes: the `metrics_p2_dual_side` constituent set + `test_metrics_route` (route pack, base-comparable subset) + `test_metrics_api_wiring` (12) | ~40 / ~300 (PROJECTION) |
| **P3-G2** | **0/1** | test-only cage bumps (`tests/unit/test_router_scope_manifest.py`) | a pin asserting the **three-way** cage form — **declared-route census == 111** · **mount census == 37** · **class manifest == 33** — plus the family scope is `maintenance:read` | **REGISTERED PACK `metrics_scope_cage_dual_side`** — composes: `test_router_scope_manifest` + `test_router_class_manifest` (both base-comparable) | ~4 / ~90 (PROJECTION) |

**Tier-2 scope discipline:** the D2 set above is **declared**; the executor never silently expands or
contracts it. The pre-exclusion registry applies to the whole-tree leg:

`regression_performance` (>300 s) · e2e specs needing a live daemon on `:8079` (→ Release Gate) ·
`tests/packs/g7` (module-level `sys.exit` collection trap — packs only).

**Whole-tree leg requirements (AM-22 provisions — part of the leg's contract, not optional):**
- identical commands on both legs; the FAILED-digest comparison (BARE-ID vs GREP-FULL, v1.0.3 form);
- 3× isolated re-runs of any diff node before classifying it as flaky; order/load sensitive reds re-run on
  both sides; net-new = full FAILED-list diff vs the **true** pre-fix base;
- **hang-class clause** — a shard that survives its cap with **no FAILED digest** (the v0.27.11 async-hang
  class) is neither a pass nor a fail: apply the **v0.27.11 compensating-evidence recipe** (scoped
  net-new EMPTY + HEAD digest sha pins + the preserved replay recipe) and record it as **disclosed
  compensating evidence**, never as a green leg;
- **budget reported against precedent** — the ≤90 min advisory is reported against the **137-min
  advisory-overrun precedent** in this repo's history, so an overrun is reported with its cause instead of
  being silently re-scoped;
- **expected-skip census declared per leg** (the `no_xdist` self-skip in `tests/conftest.py:323-336`;
  PG-gated packs skipping without `PG_TEST_ALLOWED_DBS`) — a skip delta between legs is a **finding**;
- **Scope-faithfulness.** The D2 set is the declared composition; the executor never silently expands or
  contracts it, and a scope change is reported with its reason.

**Invocations (W9/W10):** the D2 sets are the **registered packs `metrics_p3_dual_side`** and
**`metrics_scope_cage_dual_side`** (registered in `.agents/tester/PACKS.md`, invoked by pack id — never an
ad-hoc pytest list; bare `pytest <file>` is not an acceptable gate invocation). **Mandatory recipe load:**
`.agents/tester/skills/whole-tree-base-proven-diff/whole-tree-base-proven-diff.md` **or**
`.agents/tester/skills/regression-ab-partition-run/regression-ab-partition-run.md` (Tier-2 whole-tree;
consumers must NOT hand-roll slow runners) **plus**
`.agents/tester/skills/failed-digest-equality-gate/failed-digest-equality-gate.md` for the FAILED-digest
comparison. The node-cap + measured-rate projection guard runs before the leg (projected OR actual breach ⇒
STOP + report + one re-scope).

**Mutation→test table (stub):**

| Label | Mutation | Must go RED |
|---|---|---|
| `P3-M1` | the fail-closed auth seam moved after the registry read | `metrics_query_auth_matrix` |
| `P3-M2` | one route's scope dependency dropped | `metrics_query_auth_matrix` + the scope-manifest pin |
| `P3-M3` | the switch-invariance grep pin's exclusion widened | `metrics_query_switch_invariance` |
| `P3-M4` | pagination ceiling raised above the byte ceiling | `metrics_query_pagination` |
| `P3-M5` | the **listener start moved AFTER the census block** (the order AM-13 forbids: the listener starts FIRST, the sampler starts LAST of the trio) | the D1 pack `metrics_query_api_wiring` (`test_metrics_api_wiring` extension) |
| `P3-M6` | the sampler stop removed from the shutdown path | the D1 pack `metrics_query_api_wiring` |
| `P3-M7` | the **declared-route census left at 108** (the earlier docstring-based target was vacuous) | the scope-manifest declared-route-census pin (D2 leg) |
| `P3-M8` | a delta outside the 3-carrier allowlist leaked into the exposition body (or an allowlisted carrier changed shape) | the frozen-contract structural diff (AM-16) — rides the registered pack `metrics_query_routes` (approver N3; **no pack-set expansion**) |
| `P3-M9` | the response **body build** moved back onto the event loop (the `db_offload` offload removed), or the bound is not re-asserted (`max_points` ≤ **240** × ≤ 50 ⇒ ≤ **12 000 points ≈ 3.6 ms**) | the off-loop route pack `metrics_query_routes` (AM-12, build-only claim) |
| `P3-M12` | `max_points` default raised above **240**, or `?names=` accepted above 50 ⇒ the residual on-loop encode bound (> **12 000 points ≈ > 3.6 ms**) is lost | the response-bound pin — rides the registered pack `metrics_query_routes` (W6; threshold moved with the raised default, approver B1 + leader ruling) |
| `P3-M10` | the sampler start moved before the listener start (the un-implementable instruction) | the D1 pack `metrics_query_api_wiring` (AM-13) |
| `P3-M11` | `census_gated` sourced from a direct switch read in the query package | the switch-invariance grep pin (AM-18) |

## Coupling

- **Tight with Phase 2** — the provider protocol, envelope builders and sampler lifecycle.
- **Tight with Phase 1** — the contract shape.
- **Tight with Phase 5** — the FE mirrors these exact envelopes; a shape change here is a FE model change.
- **Loose with Phase 4** — family wiring feeds the registry, not the routes; the availability facts the
  routes surface do become richer once Phase 4 lands (the contract already carries the reason set).

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| The Tier-2 whole-tree leg overruns its ≤90 min advisory budget | Gate budget breach | The D1/D2 sets and the pre-exclusion registry are declared up-front; an overrun is **reported**, never used as a reason to shrink the D2 set silently. |
| The scope cage drifts from the code (cage bumped, code not — or the reverse) | Gate red / unsafe route | The cage bump rides the **same commit** as the routes (3.5); the negative leg (undeclared-mount refusal) already exists in the base. |
| A DB read sneaks into a route handler (the P-R1 incident class) | Event-loop DB pressure | The handlers are thin adapters over the provider; the provider package is pinned DB-free; the DB-backed pulls live in the sampler thread (Phase 4). |
| The switch's blast radius silently widens | Truth-table regression | `P3-M7`'s grep pin + the invariance pack + the untouched 35 gate / 29 listener / 34 census packs. |
| The frozen exposition body changes because of route registration | Contract break | `P3-M8`'s byte diff against the base artifact; registration alone adds no series. |

## Exit criterion

The three routes answer under the family's pinned scope with the fail-closed 503 seam ahead of any provider
read; the envelope matches the Phase-1 contract field-for-field; the **declared-route census reads 111**,
the mount census stays 37 and the class-manifest still reads 33; the **corrected** switch-invariance pack
proves the five truth-table invariants across the switch states with the check-site census at exactly 2;
the exposition bodies are **structurally identical to base modulo the enumerated 3-carrier allowlist**; the
lifespan start/stop order is pinned (listener → census → sampler / sampler → listener) and the provider is
not a readiness component; response bodies are built off the event loop; and P3-G1 ran as a **Tier-2** leg
with the declared D1/D2 split, a slicing proof, the pre-exclusion registry, the expected-skip census, the
hang-class clause and **12/12** mutation legs RED→GREEN (P3-M1…P3-M12 — approver N1: the W6 on-loop-encode
guard is P3-M12, so an executor stopping at 11 would never run it).
