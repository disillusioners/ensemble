# Phase 6 — Control Seam (observe-only)

**Commission:** resource-cost-control · **Spec:** `architecture-recommendation.md` §2.3 (T3 dormant columns), §2.4 (T4 `resource_limit_policy`), §2.8 (`GET /api/resources/control-capabilities`) · **Slice:** 6 of 8

---

## 1. Goal

Ship the control seam **without any enforcement**: the dormant `action`/`action_by`/`acted_at` columns on `resource_pid_map` (already created by the P1 migration — this phase asserts their dormancy), the `resource_limit_policy` table surface (already created in P1 — this phase keeps it EMPTY and exposes its existence), and `GET /api/resources/control-capabilities`. **ZERO enforcement code.** v1 is observe-only: `user_gated=true`, `enabled=false`, no seed rows, no mutation endpoints.

Note recorded for the future: O2's chromium concurrency policy (leader ruling: default cap=2, configurable 1–4, hard per-render timeout; ships in a promote train, live effect only at the next user ceremony) lands **later behind this same schema** as a `child_kind='chart_render'` policy consumed at the spawn seam. Nothing in this phase implements it.

---

## 2. Deliverables (exact paths)

| # | Artifact | Path |
|---|----------|------|
| D1 | Dormancy contract test | `tests/test_resource_control_seam.py` — asserts T3 dormant columns exist, are NULL-only, and no code path writes them; asserts T4 exists, is EMPTY after boot, `user_gated` NOT NULL default true, `enabled` default false |
| D2 | Capability endpoint | `daemon/routers/resources.py` — add `GET /api/resources/control-capabilities` → `{enforcement:"observe-only", actions:[{name,user_gated,available:false},…]}` (§2.8 shape) |
| D2b | Profile-action seam (stub only) | `daemon/routers/resources.py` — `POST /api/resources/profile` is a **seam only**: refuses when `profiling_enabled=false`, returns `409` when a profile is already active, clamps `seconds ≤ profile_timebox_s`, capability-probed; P6 owns the endpoint/stub; the profiler itself is **out of scope** (arch §2.8) — no implementation beyond gating |
| D3 | Seam docstring/ADR note | `daemon/models/resource_monitor.py` — module docstring section documenting the future enforcement port (new port behind the same schema; consumer = spawn seam for `child_kind='chart_render'`), so the dormancy is discoverable, not accidental |
| D4 | (No-schema-change note) | NO new migration files — T3/T4 DDL landed complete in P1 (including `action`/`action_by`/`acted_at`). If P1 was implemented correctly this phase adds **zero** DDL. A missing column here is a P1 acceptance failure, not P6 work. |

---

## 3. Decisions (binding — do not re-open)

| ID | Decision | Rationale (spec anchor) | Rejected alternatives |
|----|----------|------------------------|----------------------|
| D1 | Observe-only; **zero enforcement in v1**. No kill/throttle/alert code paths anywhere in this phase. | §6 ("no control implementation in v1"); comparison §6 (implement-now = 🔴 high risk, seam+dormant table = 🟢 zero rework) | implementing kill/throttle now |
| D2 | Dormant T3 columns (`action`, `action_by`, `acted_at`) are **pinned as always-NULL in v1** by test, not by convention alone — the test fails if any code path writes them. | §2.3 dormant v2 columns; §2.4 T3 | dropping the columns and re-adding later (schema rework cost) |
| D3 | T4 stays **EMPTY** in v1: no seed rows, no CRUD endpoints, `enabled` default false, `user_gated` BOOL NOT NULL default true (landed in the P1 DDL — asserted here). | §2.4 T4 + comparison §6 | seeding a default policy; shipping policy CRUD |
| D4 | `/control-capabilities` is read-only and static-shaped: `{enforcement:"observe-only", actions:[…]}` with `available:false` on every action — it advertises the seam, not capability. Same degrade/empty-shape + `{"error": …}` envelope rules as the other `/api/resources` endpoints (P1 D11). | §2.8 endpoint table | returning live policy rows (table is empty by design); auth (house posture: same-origin, no auth) |
| D5 | O2's future landing = a `child_kind='chart_render'` row in T4 consumed at the spawn seam (D3 docstring records this). O2 itself is **out of scope here** — leader ruling: ceremony-gated, default cap=2 configurable 1–4, hard per-render timeout. | §2.4 T4 note; §3 NEEDS-CEREMONY table; LEADER RULING | implementing the semaphore in P6 |
| D6 | Enforcement, when it ever arrives, is a NEW port behind the same schema — this phase must not pre-build half of it (no stub methods, no shadow config knobs beyond §2.9's). | comparison §6 ("zero rework later" requires the seam to stay clean) | stub enforcement classes |

---

## 4. Task breakdown

| ID | Task | Outcome (testable) | Touchpoints | Depends on | Risks | AC |
|----|------|--------------------|-------------|------------|-------|----|
| T1 | Dormancy contract test (D1): boot models on both dialects; assert T3 `action`/`action_by`/`acted_at` exist and default NULL; grep-audit `daemon/` for writes to those columns (only the model definition may name them); assert T4 exists EMPTY post-boot with the column defaults per D3. | Test green proves dormancy mechanically; a future accidental write turns the suite red | `tests/test_resource_control_seam.py` | P1 done | R1 | AC1, AC2 |
| T2 | `GET /api/resources/control-capabilities` in `daemon/routers/resources.py` (D2): static observe-only payload per §2.8; DI per the snapshots pattern; `asyncio.to_thread` bridge unnecessary (static shape) but repo-read of T4 row-count is allowed to prove emptiness in the payload (`policy_rows: 0`). | Endpoint returns the exact §2.8 shape; missing monitor ⇒ empty-degraded shape, never 503 | `daemon/routers/resources.py` | P1 done | R2 | AC3 |
| T2b | `POST /api/resources/profile` in `daemon/routers/resources.py` (D2b) — seam-only endpoint: refuse when `profiling_enabled=false` → 403; `409` when a profile is already active (track via in-RAM flag); clamp `seconds ≤ profile_timebox_s` (cap from §2.9 config knob); capability-probe the calling agent; never invoke a profiler, never resolve a backend, never write a row beyond a `profile_seam_invocations` counter. **Ownership:** P6 ships the endpoint + the gating tests; the profiler itself is OUT OF SCOPE (separate commission, lands behind this seam when justified). | Gating tests (4 cases: disabled-refuse, busy-409, over-cap clamp, capability-probed); row audit asserts no profiler backend invoked; review confirms zero profiler code beyond the gates | `daemon/routers/resources.py`, `tests/test_resource_control_seam.py` | P1 done | R5 | AC7 |
| T3 | Module docstring enforcement-port note (D3): document that enforcement is a future port consuming T4 at the spawn seam, naming `child_kind='chart_render'` + the O2 ruling as the first expected consumer. | Docstring present; review-checkable | `daemon/models/resource_monitor.py` | — | — | AC4 |
| T4 | Scope-fence grep gate (D1/D6): `grep -rniE "throttle|kill|enforce" daemon/services/resource_monitor/ daemon/routers/resources.py` returns only docstring/comment hits. Encode as a test assertion so the fence is mechanical. | Fence test green; any future enforcement code fails the suite until the fence is consciously lifted | `tests/test_resource_control_seam.py` | T2 | R3 | AC5 |
| T5 | Boundary green pass: full suite + confirm the P2 packs still pass (control surface adds no write paths). | Full pytest green | `tests/` | T1–T4 | — | AC6 |

---

## 5. Acceptance criteria (testable, exact gates)

Run from the worktree `/home/nea/ensemble-src-wt-resource-cost-control`:

- **AC1** — T3 dormant columns exist on both dialects, NULL-only, zero write sites (grep audit encoded in the test). Gate: `python -m pytest tests/test_resource_control_seam.py -q`.
- **AC2** — T4 exists EMPTY post-boot; `user_gated` NOT NULL default true; `enabled` default false; zero seed rows. Gate: control-seam pack.
- **AC3** — `GET /api/resources/control-capabilities` returns `{enforcement:"observe-only", actions:[…]}` with `available:false` per action; degrade-to-empty-shape holds. Gate: `python -m pytest tests/test_resource_monitor_api.py tests/test_resource_control_seam.py -q`.
- **AC4** — Enforcement-port docstring present on the models module (future-consumer path named). Gate: control-seam pack + review.
- **AC5** — Enforcement fence: mechanical grep assertion that no throttle/kill/enforce code exists outside docstrings/comments in the monitor package + router. Gate: control-seam pack (fence test).
- **AC6** — Worktree GREEN at the boundary: `python -m pytest tests/ -q` passes; branch independently mergeable. **No new migration files in this phase** (assert: `ls daemon/migrations/versions/ | grep -c resource` == 4, unchanged from P1).
- **AC7** — `POST /api/resources/profile` seam-only endpoint (D2b/T2b): 403 when `profiling_enabled=false`, 409 when a profile is already active, `seconds ≤ profile_timebox_s` enforced, capability-probed; zero profiler backend invoked (grep audit + invocation counter assertion). P6 owns the endpoint/stub; the profiler itself is out of scope. Gate: `python -m pytest tests/test_resource_control_seam.py -q` + review.

---

## 6. Risks + rollback

| ID | Risk | Impact | Mitigation / rollback |
|----|------|--------|----------------------|
| R1 | Dormancy drift: a later commit writes `action`/`acted_at` opportunistically | medium | AC1 mechanical audit fails the suite — dormancy is enforced, not hoped for |
| R2 | Endpoint implies capability the system doesn't have (FE or operator acts on it) | low | Static `available:false` shape (D4); P5 FE consumes it as informational only |
| R3 | Scope creep: "while we're here" enforcement for the chromium cap | high | D1/D6 + AC5 fence; O2 is leader-ruled ceremony-gated — any implementation here is a plan violation |
| R4 | Schema mismatch discovered late (a P1 column missing) | low | D4: P6 adds zero DDL; a missing column is a P1 AC3/AC4 failure surfaced by T1's boot test |
| R5 | Profile seam drift (T2b): the `POST /api/resources/profile` endpoint accidentally grows a real profiler invocation or writes a profile row beyond the invocation counter | medium | AC7 gates (4-case gating tests + invocation counter assertion + grep audit forbidding profiler-backend imports in the router); ownership spelled out in D2b — P6 owns the endpoint/stub only, profiler backend is a separate commission behind this seam |

**Rollback:** the endpoint and tests are additive; revert one commit. No data, no schema, no behavior to roll back — that is the point of the seam.

---

## 7. Dependencies

- **Blocks:** nothing (terminal observability slice). Future consumer: the O2 ceremony-gated chromium policy (separate commission/decisions, consumes T4 at the spawn seam).
- **Blocked by:** P1 (T3/T4 DDL + router must exist). Logically sequenced after P2–P5 in the slice order but adds no requirement on them — it could merge any time after P1.
- **Rides alongside:** O2 (ceremony-gated, lands behind this schema later); P8 docs (documents the observe-only posture).
