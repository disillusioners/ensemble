# Approach Comparison: Command-Intercept Seam (`/compact` Slash-Command Subsystem)

Date: 2026-08-31
Question: Where does a `/`-prefixed message enter the command subsystem — router intercept inside `POST /messages`, a dedicated commands endpoint, or a hybrid of both?
Method: Competitive fan-out — 1 worker (`trade-off-analysis`, instance `1efd887c`), 3 approaches, five fixed axes, weighted scores (Complexity 20% / Scalability 20% / Maintainability 25% / Risk 20% / Cost 15%). Full analysis in the worker report; summary table reproduced for the record. Scores 1–5 (5 = better).

## Approaches

- **A (plan baseline):** router intercept inside `POST /messages` (`daemon/routers/messages.py`, after :240 validation, before :243 status capture) → service-layer `CommandRegistry` dispatcher. FE sends `/compact` as a normal message; BE parses the prefix; one check covers all four status branches.
- **B:** dedicated `POST /api/instances/{id}/commands` (+ `GET /commands/{command_id}`); FE detects `/` client-side and routes there; `POST /messages` byte-identical for all traffic.
- **C (hybrid):** intercept as the UX entry AND dedicated endpoint as the programmatic surface, both resolving through the SAME registry/dispatcher.

## Comparison

| Axis (weight) | A: Router intercept + dispatcher | B: Dedicated endpoint | C: Hybrid |
|---|---|---|---|
| **Complexity** (20%) | **4** — 4–6-line intercept; one service file (~200 LOC); one FE adapter (`parseCommandAck`); one transport | **3** — new route + dispatcher + FE slash-detection + second auth/header contract (drift hazard) | **2** — two transports + two thin adapters + shared registry; three things to document |
| **Scalability** (20%) | **5** — command #2/#3 = one `CommandSpec` registration, ZERO router edits; dispatcher is the natural agent-tool surface later | **4** — extensible, but each client re-implements the FE parser; drift accumulates per transport | **4** — same zero-router-edit property; convergence rules add overhead |
| **Maintainability** (25%) | **4** — single source of truth at registry AND transport; one FE contract; uniform applicability/rate-limit/enable enforcement | **2** — per-command contract drift on a second transport; non-FE clients re-implement slash parsing; FE chat flow special-cases input | **3** — registry-level truth preserved, transport-level truth doubled (two error shapes, two test matrices) |
| **Risk** (20%) | **4** — touches the daemon's highest-traffic endpoint, but the diff is contained; status-branch bodies untouched; SSE rides zero-cost custom-event path (live_event_hub.py:150-173); byte-identity enforceable by test | **4** — `POST /messages` untouched; but split transport = two failure modes; unknown-command validation can't reuse the :222-240 pipeline; SSE needs instance-scoping re-derivation | **3** — both surfaces touched; byte-identity depends on wiring BOTH correctly; two adapters = two test matrices |
| **Cost** (15%) | **5** — ~1 router-function edit + ~200 LOC service + FE adapter | **4** — ~100 LOC router + ~50 LOC FE parser + adapter | **3** — most code across all layers |
| **Weighted total** | **4.35** | **3.30** | **3.00** |

## Recommendation

**Approach A.** Dominant axis: **Maintainability** (4 vs 2 vs 3) — a single transport keeps the command surface in one place for FE, tests, and future non-FE callers; B's purity ("messages endpoint untouched") is bought with per-client parser duplication; C pays double transport cost for a programmatic surface no current consumer needs. Risk is acceptable because the intercept is a prefix check between validation and status capture, with byte-identity for non-command traffic enforceable by an explicit regression test.

Flip conditions (both recorded, neither expected in this feature's horizon):
1. `POST /messages` strict byte-identity becomes a compliance/audit requirement → **C**.
2. A second programmatic (non-FE) client lands that mandates an addressable endpoint → **C** as the bridge (A and B both retrofit into C).
