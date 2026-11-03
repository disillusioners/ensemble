# Plan Overview: metrics-agent-tools

Date: 2026-11-03 (UTC)
Author: planner[v2] via plan-creation worker (commission: executable plan set for the agent-facing metrics tool surface — item #2 of the ratified metrics sequence)
Status: Draft — composed directly from the BINDING design (`technical-analysis.md`, 1755 L) plus the three research files in this directory; planner adjudications **P-1..P-7** are binding and folded into `decisions.md`.
Feeds: architect enrichment → plan council (reviewer) → fresh approver → implementation (developer, this worktree) → code council → tidier → tester functional gate → giter staging (NO live promote — user nonce ceremony owns it)
Branch / worktree: `feature/metrics-agent-tools` · `/home/nea/ensemble-src-wt-metrics-tools` (base `origin/latest @ 03a2297c3180e79c6b5c26a92b2caca7c0452725`)
**Version discipline:** this plan names NO version. The version slot is derived by giter at stage time (W9); **no version string for this lane appears anywhere in this plan set**, and no implementation lane may mint one early. **Worktree discipline:** every artifact lands in `/home/nea/ensemble-src-wt-metrics-tools`; the main checkout `/home/nea/ensemble-src` is NEVER written; all git operations (branch, merge, bump, tag, stage) belong to giter.

---

## Objective

Give the daemon's own agents a **sanctioned, contracted, in-process read surface** over the frozen central-metrics query substrate: ONE privileged read-only category `metrics-read` with THREE flat tools — `metrics_families` / `metrics_snapshot` / `metrics_history` — allow-listed to `maintenancer` / `devops` / `wanderer`, gated by the house two-layer pattern (privileged category for visibility + in-body `agent_id` gate for authority), reaching the ONE shared provider through an additive manager-held mount, plus the D6-named `metrics:read` scope split on the HTTP door (mint the scope, re-point the three query routes to the any-of set, **route count stays 111**), plus the FE `registry_read_ok` mirror fold — and **no live promote**.

Single-sentence completion test: the phases complete at the **giter hand-over (task 6.5)** — STAGING is the giter-lane step that FOLLOWS the phase plan (never a phase task), and the staged (not promoted) release it produces is one in which each of the three holder agents resolves exactly the three new `metrics_*` tools (an empty-allow agent resolves zero), every tool call is authorized by the in-body gate BEFORE the provider is touched and reads the SAME provider/ring as the HTTP door through `manager.metrics_query_provider`, the scope taxonomy carries `metrics:read` with the three query routes accepting `maintenance:read|metrics:read` while the declared-route census stays **111**, the FE `/metrics` page renders a DISTINCT degraded surface (never the error banner, never the empty panel) when `activation.registry_read_ok === false`, all new packs are registered in `.agents/tester/PACKS.md` with re-derived `--collect-only` counts, and no promote is performed.

**Rollback posture (council N6, for the ceremony record):** structurally safe — no promote, no migration, no new dependency, `maintenance:read` retained (zero behaviour change for any existing holder), staged-not-promoted; an auto-rollback can fire safely.

**Design authority.** The design is DONE — `technical-analysis.md` is binding input; this plan set composes its execution and does not redesign it. Where the planner had to adjudicate (E-1, fold-(b) unresolvability, FE-mirror shape, route-count arithmetic, audit posture, rulings restatement, watch-doc scheduling), the adjudications **P-1..P-7** below are binding and are folded verbatim into `decisions.md` alongside the design's own D-UX/D-SCOPE/D-WATCH decisions.

---

## Dispatcher adjudications (BINDING — full text in `decisions.md`)

| ID | Ruling (binding) | One-line rationale |
|---|---|---|
| **P-1** | **E-1 ADJUDICATED PROCEED (2026-11-03, lane owner — recorded verbatim in `decisions.md`).** Mint `metrics:read` + re-point the three routes to any-of + update the two scope-manifest pins **AND apply the pre-specified description-only contract-prose amendment (3.6b) as PRIMARY — in the SAME commit as the re-point** (the FIVE canonical line-ranges `:31-32`, `:318-322`, `:379-380`, `:387`, `:404-405` — `:387` is IN per **E-1 amendment-2** (2026-11-03); nothing outside the five ranges changes; the contract-doc pin stays green BY CONSTRUCTION with the amendment applied — protected by the verbatim-preservation list, never re-pinned). The debt-record path (3.6a) is the FALLBACK — on guardrail (d) objection OR on amendment-2's no-viable-wording return. | D6 is the contract's own pre-named, user-ratified commitment — prose execution is contract COMPLETION, not drift; the frozen reservation protected the WIRE contract, which the amendment never touches. Full ruling + guardrails (a)-(d): `decisions.md` Part 4. |
| **P-2** | Fold (b)'s six numbered tidier Mediums (items 3, 5, 10, 16, 17, 21) are a **PROVENANCE GAP** — routed to the implementation lane's own tidier pass, which records the numbered mapping in `fold-ledger.md` §G at that time. **No invented mappings.** | The tidier's per-item list was never committed (research-folds §2.2); the plan does not fabricate findings. |
| **P-3** | FE mirror shape: **optional 4th member + `=== false` predicate** (mirrors the BE's `getattr(..., True)` default); the degraded state is a **DISTINCT badge/hint surface** — it must NOT render the error banner, preserving the existing pin *"a 200-EMPTY response never renders the error banner"* (`metrics.component.spec.ts:15`). | Default-safe mirrors the BE; a distinct surface keeps the 200-EMPTY pin true while making failure-vs-empty assertable. |
| **P-4** | **Route-count correction is binding: 111 today, zero new routes.** Mount census 37 and class-manifest families 33 unchanged; the `ari` trailing-entry allow pin untouched (metrics is not granted to ari). Any plan step that bumps a route count is wrong. | The 108→111 transition already happened in lane #1 (`tests/unit/test_router_scope_manifest.py:398-401`); a tool is not a route (D-SCOPE-6). |
| **P-5** | **No audit ledger** for the read-only category (house precedent `instance_read.py:62-64`, `wedge_capture.py:30-32`); the 4-token fail-closed ladder (`unknown-agent` / `not-authorized` / `metrics-unavailable` / `invalid_label_filter`) replaces HTTP 401/403/503/400 semantics in-process. | A read writes no row; the ladder is the in-process mirror of the door's status semantics (D-SCOPE-4, D-UX-14). |
| **P-6** | Rulings **R1–R6 all stand**: flat tools; the scope split is THIS lane's deliverable; pull-only (no cursors/alerts in v1); ONE category, three holders; no audit ledger; no cursors exposed to agents. | Confirmed by the design (§A.0 R1, §B.5 R2, §C R3, §A.0 R4, R5, §A.5 R6) — restated so no gate re-litigates them. |
| **P-7** | The watch/alert design doc is authored **DURING IMPLEMENTATION as a close-out artifact** from the design's Section C outline (Phase 6, final task) — it is not a planning deliverable of this plan set. | Section C is deliberately "questions with a recommended shape"; authoring it now would be redesign (D-WATCH-1). |

---

## Scope

### In Scope

1. **Seam enablement (Phase 1)** — the additive `MetricsQueryProvider` Protocol amendment (`now` / `max_series` kwargs + the `registry_read_ok` property — **AmRULING-3: the amendment is GAP-1/GAP-2 + GAP-3 only; `listener_enabled`/`census_enabled` are DROPPED (the tool consumes `activation_block(provider)` instead)**), the `envelope.read_numeric_fact` promotion, the `InstanceManager.metrics_query_provider` mount (class default + typed property), the **THREE-site** lifespan mount beside `app.state.metrics_query` (the mount at `api.py:2533`, the boot-degradation clear at `:2541`, the shutdown/cleanup clear at `:3778` — council W4) plus the lifecycle-triple pin (task 1.6(ii-c)), and the two-fake mechanical cost.
2. **Category + tools (Phase 2)** — `daemon/tools/metrics_read.py` (three flat tools, two-layer gate, 4-token refusal ladder, projection envelope, six-member bound block, availability-verbatim carriage, generic dark-with-live caveat, sane-default ladder, family resolution, hard floor, no-cursor posture), the four registry edits, the factory import + `*_if_available` wrapper + placement-constrained extend, three `meta.json` allow entries (CATEGORY ID ONLY) + three `tools_note.md` sections, the SAME-COMMIT pin fan-out (10 files / 15 EDIT sites + 1 VERIFY-ONLY), the new registration pack, the frozen-drift-pin co-landing, and the name-collision guard.
3. **Scope split (Phase 3)** — the `metrics:read` taxonomy line, the three-route any-of re-point, the two manifest-pin moves (family scope + scope-cage `FAMILY_SCOPE`), the route-count NON-movement verification, the `metrics:read`-bearer auth-matrix extension, and **E-1 ADJUDICATED PROCEED: the contract-prose amendment (3.6b) primary, same-commit, guardrails (a)-(d); the debt-record path only as the guardrail-(d) fallback**.
4. **FE mirror fold (a) (Phase 4)** — the optional `registry_read_ok` activation member, the degraded shell state, the DISTINCT badge/hint surface, the fixtures, the spec legs, the e2e degraded leg, the type-check + both build legs.
5. **Verification + docs (Phase 5)** — PACKS.md registration, the mutation matrix, the tool-ladder auth pack, the persona-shaped acceptance tests (both personas from `technical-analysis.md` §A.12), shell packs, the tools-surface docs verification (loader/help/tools_note), the prompt-artifact re-stamp implication record, the evidence pack.
6. **Close-out (Phase 6)** — the watch/alert design-outline doc authored from Section C (P-7), the fold-ledger numbered-mapping backfill protocol (P-2), the stage-prep checklist (EMPTY migration delta expectation; `agents/` tree delta; no version; no promote), the final consistency sweep.

### Out of Scope — VERBATIM fences

- **The frozen contract is frozen** (`metrics-query-contract.md:10-12`). The tool layer adapts to it, never changes it — with the ONE named exception the lane owner has adjudicated (P-1 / E-1, 2026-11-03): the D6 `metrics:read` prose amendment, STRICTLY limited to the FIVE canonical line-ranges (`:387` is IN per E-1 amendment-2), same-commit with the re-point, pinned blocks byte-identical (green by construction — verbatim-preservation list enforced). The WIRE contract is untouched: no wire shape, route, error-code, availability-reason, or invariant edit ships from this lane; `schema_version` stays **1** and `vocabulary_version` stays untouched by this lane (the tool ECHOES both, never gates on them — AM-7 discipline).
- **No alert / watch / trigger machinery** (R3, D-WATCH-1): no arming tool, no schedule row, no firing invariant, no delivery path. Only the Section-C outline doc ships (P-7).
- **No act half**: no write tool of any kind; the category is structurally read-only (wedge-capture precedent). No second `metrics-act` category.
- **No audit ledger, no migration, no new table** (P-5, D-UX-14) — the migration delta is expected **EMPTY**.
- **No new npm dependency, no new backend dependency** (the FE fold touches existing files only; `package.json` diff must be EMPTY).
- **No kill-switch** for the category (D-UX-15); the off lever is removing the three allow entries.
- **No agent-scoped narrowing** of the category per holder (one category, one allow-set, R4) — the critic-pattern deny-list narrowing is not in scope.
- **The out-of-lane defect set stays OUT** (P-2 + research-folds §4): R-i (census `gated` polarity inverted — `metrics_census.py:323-335`), R-ii (`capture_active` never wired — `api.py:2527-2533`), R-iii (F7 histogram never publishes), R-iv (`stale_pause_sweep` never published), `family_a_dbpool_baselines` (stale at base), `pg_census_fence_shadow` (pre-existing red). The tool **tolerates** these (the generic dark-with-live caveat, D-UX-7) and NEVER fixes or attributes them.
- **The frozen contract's `:104-106` activation shape lists THREE members while the BE emits FOUR** (`registry_read_ok`) — pre-existing doc drift (research item F6 / approver N10): KNOWN-RESIDUAL owned by the contract owner (fold-ledger §B), visible to the task 6.4 sweep — NOT edited by this lane (outside the five canonical ranges).
- **The six numbered tidier Mediums (3, 5, 10, 16, 17, 21)** are a provenance gap — routed, not planned (P-2).
- **No live promote** — the user's 3-factor nonce ceremony owns it. No version minting.
- **Grafana (item #3)** stays deferred (D6's second deferred lane).

---

## Phase map

| Phase | Name | Objective | Tasks | Gate tier | Status |
|---|---|---|---|---|---|
| **1** | Seam enablement (Protocol + provider reach) | Extend the Protocol BEFORE typing the consumer (the house lesson), promote the public numeric-fact reader, mount the ONE provider on the manager, wire the lifespan, and pay the two-fake cost — with a seam pin pack proving all of it. | 8 | **Tier 2** (`daemon/manager.py` + `daemon/api.py` — mechanical core-seam list) | pending |
| **2** | Category `metrics-read` + the three tools + the pin ceremony | Author `daemon/tools/metrics_read.py` to the design's full surface, land the four registry edits + factory wiring + allow entries + tools_note sections + the 16-site pin fan-out + the registration pack + the drift pin **in ONE commit cluster**. | 10 | **Tier 1** (blast-radius leg; no core-seam file) | pending |
| **3** | `metrics:read` scope split + E-1 | Mint the scope, re-point the three query routes to the any-of set, move the two manifest pins, prove the route count does NOT move, extend the auth matrix for the new bearer scope, execute the E-1 contract-prose amendment as PRIMARY (ADJUDICATED PROCEED — same-commit, guardrails (a)-(d); the debt-record path only as the guardrail-(d) fallback), and forward-declare the item-#3 text-route seam in the API doc (AmA-8). | 8 | **Tier 1** (scope-cage dual-side 12-node pack + D1 pack) | pending |
| **4** | FE `registry_read_ok` mirror (fold a) | Add the optional 4th activation member, the degraded shell state and its DISTINCT surface, reconcile with the 200-EMPTY pin, land fixtures + spec legs + the e2e degraded leg + the type-check/build legs. | 8 | **Tier 1** (FE pack + e2e + 2 build legs) | pending |
| **5** | Verification + docs | Register every pack with re-derived counts, build the mutation matrix, extend the auth-matrix pattern for the tool ladder, convert the two persona examples into structural assertions, land shell packs + docs-surface verification + the evidence pack. | 8 | **Tier 0** (docs + test-only diff → changed-pack PASS + the new packs' own legs) | pending |
| **6** | Close-out | Author the watch/alert outline doc from Section C (P-7), execute the fold-ledger backfill protocol (P-2), assemble the stage-prep checklist, and run the final consistency sweep. | 5 | **Tier 0** (docs-only) | pending |

**Grouping rationale (context reuse — why these six):**

- **P1/P2 split (seam vs consumer), kept separate despite both touching the tool call path:** (a) the phase ORDERING *embodies* the house lesson — the Protocol is extended before any consumer exists, so a reviewer reads the seam diff with nothing else in flight; (b) P1's diff is minimal and its Tier-2 whole-tree leg therefore triages cheaply, while P2's Tier-1 blast-radius leg stays scoped to the tool surface; (c) the ONE-commit-cluster constraint binds P2's *internals* (registry + pins + allows + module), and pulling the seam into P2 would widen that cluster to `manager.py`/`api.py` for no reuse gain. The dispatch skeleton's "factory" is moved from P1 to P2 — **why:** `create_metrics_read_tools` imports the tool module and is meaningless (and un-importable) before it exists; grouping factory + wrapper + extend with the module keeps the frozen-drift pin's source closure green within a single commit.
- **P3 separate from P2:** different review audience (auth/scope vs tool-registry), different pins (scope cage vs privileged-set pins); the E-1 adjudication (2026-11-03) RESOLVED the accept/reject decision point — the amendment is primary, and P3's remaining review burden is guardrail (d): the plan council and fresh approver still review the amendment, and a substantive objection returns it to the lane owner (3.6a is the documented fallback).
- **P4 (FE) last of the code phases:** it depends on nothing from P2/P3 at the wire level (the BE already publishes `registry_read_ok`), but it is sequenced after them so the FE gate runs against the settled daemon surface and the lane's only FE churn stays one reviewable unit.
- **P5 (verification) after all code:** PACKS.md rows and mutation legs must register the FINAL surfaces (counts re-derived, never remembered — PACKS.md:16-19 discipline).
- **P6 (close-out) mirrors lane #1's phase-6 shape:** docs/evidence/stage-prep closure with the two hard prohibitions (no version, no promote).

**Hard ordering:** P1 → P2 → P5's registration of the P2 packs; P3 is order-independent of P2 (different pin families) but must precede P5's auth-matrix extension; P4 is order-independent of P2/P3; P6 last.

---

## Coupling map

| | P1 Seam | P2 Tools | P3 Scope | P4 FE | P5 Verification | P6 Close-out |
|---|---|---|---|---|---|---|
| **P1** | — | tight (the factory resolves the mount; the tools are typed against the amended Protocol) | loose | loose | tight (seam pins registered) | loose |
| **P2** | tight | — | loose (different pin families) | independent | tight (packs + mutation legs) | tight (ledger) |
| **P3** | loose | loose | — | independent | tight (auth-matrix extension) | loose |
| **P4** | independent | independent | independent | — | tight (FE pack + e2e rows) | loose |
| **P5** | tight | tight | tight | tight | — | tight (evidence pack) |
| **P6** | loose | tight | loose | loose | tight | — |

Shared-file couplings: `daemon/services/metrics_query/protocol.py` + `envelope.py` (P1 author, P2 consumer, P5 pins); `daemon/tools/instance.py` (P2 only — P1 does NOT touch it); `tests/unit/metrics/` (P1 seam pack, P3 cage pack, P5 registrations); `.agents/tester/PACKS.md` (P5 owns ALL new rows); `frontend/src/app/{models,pages}/metrics/` + `frontend/e2e/` (P4 only).

---

## Risk register

| ID | Risk | Impact | Mitigation |
|---|---|---|---|
| **R1** | **Pin-ceremony same-commit discipline** — a split commit (registry entry landed, pins not yet bumped, or vice-versa) leaves a RED exact-set pin mid-lane and trips the bidirectional frozen-drift pin | High (a red privileged-set pin is a lane-blocking state visible to every concurrent reader of the tree) | P2 task 2.9 makes the ONE-commit cluster an explicit acceptance criterion covering ALL of: `metrics_read.py` + the four `_tool_registry.py` edits + `instance.py` extend + 3 `meta.json` + the 10-file/15-EDIT-site pin fan-out (plus the 1 VERIFY-ONLY ari trailing-entry site) + the registration pack + the drift pin; the registration pack (legs a–j + A13) is run at the phase gate; the pin set is re-derived by grep at implementation time, never trusted from prose (`_tool_registry.py:347-351`, research-tool-conventions §3.2) |
| **R2** | **Protocol amendment breaks the two existing fakes** — `runtime_checkable` `isinstance()` checks member presence, so any fake missing a newly-declared member stops satisfying the Protocol (`RecordingProvider` at `tests/helpers/metrics_query_fixtures.py:47`; `_Fake` at `tests/unit/metrics/test_metrics_query_provider.py:557-572`) | Medium (red fixtures-dependent packs across the metrics corpus) | The design already audited it: both fakes' `history(**kwargs)` absorbs `now=`/`max_series=`; `RecordingProvider` needs exactly `registry_read_ok = True`; `_Fake` grows **EXACTLY ONE member** (`registry_read_ok = True` — council W2/B2: the trimmed Protocol declares no GAP-4/5 members, so the fake cost SHRINKS three booleans → ONE; per `decisions.md` Part 5 AmRULING-3 + task 1.5) (technical-analysis §A.2 :192-196). P1 task 1.5 pays it BEFORE any consumer exists; the seam pin (1.6) asserts the conformance so the failure mode is loud, not silent |
| **R3** | **FE mirror breaks the 200-EMPTY pin** — a failed registry read IS a 200-empty response; naively routing it to the error banner invalidates the existing spec pin "a 200-EMPTY response never renders the error banner" (`metrics.component.spec.ts:15`) | Medium (an existing FE pin reds; the degraded state becomes indistinguishable from a transport failure) | P-3 binding: the degraded state renders a DISTINCT badge/hint (`data-test="metrics-registry-degraded"`) — never the error banner; `showEmptyPanel` gains the degraded guard so the empty panel does not fire; Phase 4 task 4.5 lands BOTH the positive degraded leg AND the negative healthy-200-empty leg in the same change |
| **R4** | **The dark-with-live caveat drifts into defect attribution** — a shipped tool string that hard-codes "R-ii"/R-i goes stale the moment the owning lanes land, and falsely attributes causes the tool cannot verify | Medium (a lying shipped string in a contract-adjacent surface) | D-UX-7 binding: the caveat is GENERIC (asserts only the runtime-verifiable fact dark+live); Phase 5 task 5.2 pins the caveat string shape AND a pin asserts the shipped strings contain NO defect-attribution token (`R-i`, `R-ii`, `capture_active`, `census inverted` absent from `metrics_read.py`) |
| **R5** | **Name collision / semantic proximity** — `skill_get_metrics` (`_tool_registry.py:1534`, the `dynamic-skill` category) is the only existing `metrics`-containing name; `snapshot_create`/`snapshot_search` mean Agent Snapshot, not metrics | Low (exact-match registry ⇒ no mechanical collision) but Medium if a docstring conflates the two "snapshot" concepts | Design §A.0 verified no collision (exact-match name set; `metrics-read` absent from `PRIVILEGED_TOOL_CATEGORIES`, `DYNAMIC_TOOL_NAMES`, `KNOWN_TOOL_NAMES`); P2 task 2.10 re-verifies the three name sets by grep at implementation time; the `metrics_snapshot` docstring + `CATEGORY_DOC` carry the mandatory disambiguation sentence (design §A.0 :75) and the registration pack pins it |
| **R6** | **"Wanderer is read-only" is FALSE in fact** — wanderer holds `bash`/`proc`/`filesystem`/`db`/`infra`/`service` (research-tool-conventions §5.1); a reviewer could object that granting it a daemon-telemetry read widens an already-permissive agent | Low | The category is structurally read-only by construction (no write tool exists in it), which is the wedge-capture 3-holder precedent (`wedge-capture`: ari/leader/maintenancer, research-tool-conventions §0/§5.3); the surface is strictly less sensitive than wedge-capture (all-thread stack dumps + per-thread CPU maps — technical-analysis §B.4 :1434); this is disclosed in the plan, not "fixed" (no agent allow-set narrowing in scope) |
| **R7** | **Worktree discipline breach** — an implementation step writes to the main checkout `/home/nea/ensemble-src` or performs git surgery outside giter's lane | High (cross-lane contamination; the main checkout carries other lanes' state) | The overview header binds it; every phase's gate leg runs in the worktree with `WT=` fence form (research-folds §3.1); P6 task 6.3 hands git ops to giter explicitly; no task in this plan touches `/home/nea/ensemble-src` |
| **R8** | **Route-count regression** — an implementer "fixes" a count pin to a remembered number (108, or bumps 111 for the tool) | Medium (a wrong pin masks a real route drift, or reds the cage pack spuriously) | P-4 binding: 111 today, zero new routes, mount 37, class-manifest 33; P3 task 3.4 is a verification task that asserts the count pins are UNCHANGED in the diff and re-derives them via `--collect-only`/manifest introspection at gate time |
| **R9** | **The amendment escapes the adjudicated boundary** — a contract-doc edit lands outside the FIVE canonical line-ranges (E-1 amendment-2), or the contract-doc pin is RE-PINNED to absorb the amendment instead of staying green by construction, or the council/approver objection path (guardrail (d)) is mishandled (the amendment proceeds past a substantive objection, or the fallback fires without a re-adjudication) | Medium (a contract-doc edit wider than the ruling, or a pinned-block change, is a commission-boundary breach) | Guardrails (a)-(d) binding (P-1 / E-1, `decisions.md` Part 4): task 3.6's acceptance asserts the contract-doc diff touches ONLY lines within the FIVE canonical ranges (E-1 amendment-2), the `central_metrics_contract_doc` pack (21 nodes) green WITH THE AMENDMENT APPLIED — expectations untouched via the verbatim-preservation list (a re-pinned pack is a gate RED), the amendment in the SAME commit as 3.2/3.3, and the fallback (3.6a debt record) reachable ONLY after a council/approver substantive objection + lane-owner re-adjudication OR amendment-2's no-viable-wording return |
| **R10** | **Gate-leg count memory** — PACKS.md rows registered with remembered node counts | Low-Medium (a stale count is a recorded finding, never a silent correction — but a remembered count invites one) | P5 task 5.1 mandates `pytest --collect-only -q` re-derivation for every row at registration time ("record the moment, never the memory" — PACKS.md:69-76 discipline, research-folds §3.3) |
| **R11** | **Prompt-artifact drift at stage** — P2 changes 3 `meta.json` + 3 `tools_note.md` (the `agents/` payload tree); council W7: the BASELINE-tracked delta is the **3 `tools_note.md` ONLY** (`meta.json` is not in `VERSIONED_PROMPT_ARTIFACTS` — `daemon/tools/prompt_artifacts.py`), so the baseline moves on exactly 3 files | Low (a stage-time surprise, not a defect) | P5 task 5.6 records the re-stamp implication with the derivation; P6 task 6.3 declares BOTH deltas — the `agents/` tree delta (6 files) and the baseline delta (3 files) — in the stage-prep checklist so giter's baseline check is pre-armed |
| **R12** | **Env-fence failure in a gate leg** — ambient `POSTGRES_*` / `PORT` leaking into a pack run (the glob `env -u POSTGRES_*` form is a SILENT NO-OP) | Medium (a "fenced" run hitting real state) | Every P5 PACKS.md row carries the house per-variable scrub form verbatim (`env -u POSTGRES_USER -u POSTGRES_DB -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_PASSWORD -u DATABASE_URL -u PORT` — research-folds §3.1); this lane plans NO daemon run, NO frozen-binary smoke, and NO PG leg (read-only tools over published state — the existing PG census pack is untouched), so the fence surface is minimal |
| **R13** | **Tool output/context blowup** (AmM-1) — the tool path inherits NONE of the door's output bounds; at the old ceilings the returned leg breached MAX_TOTAL_POINTS by 240 pts (~70k tokens in one turn) | Medium pre-fold → **low residual after the fold** | AmM-1 binding: returned-leg clamp ≤ 12 000 (`max_points ≤ MAX_TOTAL_POINTS // (top_n+1)`), tool-local byte ceiling with the door's trim + six-member bound block, compare-leg work accounted separately, the default-call cost stated in CATEGORY_DOC (Part 5 AmM-1; phase2 tasks 2.2/2.4; phase5 5.2 legs) |
| **R14** | **Event-loop interaction of the tool bodies** (C-1) — plan silence on sync vs async could have landed the body build on the main loop | **Resolved NEGATIVE with proof** (architect §0: the provider read path is in-memory end-to-end — no psycopg/persistence import; all DB work lives in the sampler thread); placement now PINNED by RULING-4 / D-UX-16 (`async def` + `db_offload.offload`, mirroring `routers/metrics.py:441-451`); worst-case on-loop build ~3.7 ms even unoffloaded (the house AM-12 figure — provenance recorded, re-measure only if the pack wants a latency leg) | RULING-4 binding (Part 5); the registration-pack callable-shape leg (phase2 2.9) |
| **R15** | **Missing-mount behaviour** (C-3) — the `_if_available`-returns-`[]` shape would have made the metrics surface VANISH SILENTLY on degraded boot and left the `metrics-unavailable` ladder token unreachable | **Resolved by RULING-1 (Option A)** — tools stay PRESENT, the body reads the mount per call, the typed refusal fires loudly (the notify_user kill-switch shape, `notify_user.py:709`); pre-ruling this was a silent-vanish defect | RULING-1 binding (Part 5 AmM-3); the degraded-boot leg (phase2 2.9; phase5 5.3) |
*(AmC-6 re-rank note: R7 stays High for PROCESS (worktree discipline) but no longer outranks the now-closed runtime items; R1-R6/R8-R12 verified REAL with cited evidence — content unchanged.)*

---

## Success criteria

### Per phase (measurable end-states)

| Phase | Criterion | How to measure | Threshold |
|---|---|---|---|
| P1 | The Protocol declares every member the tool layer calls; the ONE provider is manager-mounted; zero contract-surface change | seam pin pack + identity pin + grep of the P1 diff | the seam pin reds on any member deletion; `manager.metrics_query_provider IS app.state.metrics_query` in the lifespan fixture; `envelope.py` diff = exactly the 1-line promotion; `central_metrics_contract_doc` (21 nodes) green; existing metrics packs net-new reds = 0 |
| P2 | The category exists, is default-deny, and is held by EXACTLY {maintenancer, devops, wanderer}; the three tools answer from the shared provider behind the two-layer gate | registration pack (legs a–j + A13 attribute/default-deny/fail-closed/holder-census) + the 16-site fan-out green + the drift pin green | `len(PRIVILEGED_TOOL_CATEGORIES) == 17` at all five count pins; an empty-allow agent resolves ZERO metrics tools; `""`/`None`/`123` agent ids all refused fail-closed; the holder census finds the category in exactly the three metas and NOWHERE else; the whole cluster is ONE commit |
| P3 | `metrics:read` exists; the three query routes accept `maintenance:read|metrics:read`; the text route + the route censuses are untouched; the E-1 amendment applied per the adjudication | scope-cage pack (7) + `metrics_scope_cage_dual_side` (12) + the extended auth matrix + the manifest diff | the family scope pin reads `"maintenance:read\|metrics:read"`; declared-route census == **111**; mount census == **37**; class-manifest == **33**; the ari trailing-entry pin byte-unchanged; a `metrics:read`-only bearer reads the three query routes (200) and is refused on the text route and on checkpoint cleanup (403); the contract-doc pack green **WITH THE AMENDMENT APPLIED** (guardrail (b) as restated: by construction — the verbatim-preservation list holds, never re-pinned; the diff touches ONLY lines within the FIVE canonical ranges — guardrail (a) as amended; same-commit with the re-point — guardrail (c)) |
| P4 | The FE mirrors the 4th activation member default-safely; a failed registry read renders the DISTINCT degraded surface — never the error banner, never the empty panel | component/model specs + the e2e degraded leg + scoped type-check + both build legs | degraded leg green (badge visible, `metrics-empty-panel` absent, `metrics-catalog-error` absent); healthy-200-empty leg green (no banner, no badge — the `:15` pin intact); `package.json` diff EMPTY; type-check green via `frontend/`-local toolchain only |
| P5 | Every new pack is registered with re-derived counts; the mutation matrix is meaningful; the persona surfaces are pinned | PACKS.md rows + the mutation harness log + the persona spec | every row carries a fresh `--collect-only` count; every mutation leg RED→restore(byte-identical)→GREEN with a dirty-tree refusal; the two persona examples' shapes/vocabularies/field names assert structurally (never on observation-dependent values) |
| P6 | The outline doc exists and adds no machinery; the ledger backfill protocol is staged; stage-prep is complete minus version+promote | doc diff + the fold-ledger §G placeholder + the checklist | the outline cites Section C and plans NOTHING (design-only); §G records the backfill protocol verbatim; the checklist has every item ticked EXCEPT the version slot and the promote |

### Lane-level (the lane is done when)

| # | Criterion | How to measure | Threshold |
|---|---|---|---|
| L1 | **Staged, not promoted** | the stage journal + the symlink | no promote performed; the live release untouched; no promote command run in the lane |
| L2 | **One provider, one ring, one truth** | the identity pin + a tool-vs-door read of the same series in a fenced fixture | the tool's provider object IS `app.state.metrics_query`; no second `RegistryMetricsQueryProvider` construction exists in the diff (grep) |
| L3 | **The two-layer gate is unbypassable** | the registration pack + a literal-tool-name probe | a synthetic meta listing `"metrics_families"` as a literal resolves it ONLY for a holder agent id and the body gate still refuses non-holders (the allow list never reaches the body gate — D-SCOPE-2) |
| L4 | **A bound that drops something is never silent** | the six-member-bound-block pins | `matched_count`/`returned_count`/`dropped_count`/`more_available`/`truncated`/`narrow_hint` present and semantically correct in all three tools; families/snapshot counts exact; history `returned_count` exact + `more_available` boolean; every `narrow_hint` names a CONCRETE next filter |
| L5 | **Availability is carried verbatim, deltas never fabricated** | the availability + delta pins | the tool emits the provider's `availability` block unchanged (no re-derivation, no re-order, no substitution); `<2 points` ⇒ `null` + `delta_reason: "insufficient_points"`, never `0` |
| L6 | **The refusal ladder mirrors the door** | the tool-ladder auth pack | `unknown-agent` / `not-authorized` / `metrics-unavailable` / `invalid_label_filter` each reachable; authorization fires BEFORE the substrate check and before any provider read (the ordering pin) |
| L7 | **No-cursor posture** | grep + the args-schema pins | no tool accepts or returns a cursor; every provider call is re-anchored (`now=instant`, compare leg at `instant − window_s`) |
| L8 | **Scope split landed with zero route churn** | the manifest/cage diff | taxonomy +1 scope; three decorators any-of; declared-route census 111; mount 37; class-manifest 33 |
| L9 | **Fold (a) closed** | the FE diff + specs | `MetricsActivation` carries optional `registry_read_ok`; the degraded surface exists with its own `data-test`; the 200-EMPTY pin and the P5-M5 field-set pin (parses §4.3, unaffected) both green |
| L10 | **Zero migration, zero dependency, zero kill-switch** | `daemon/migrations/versions/` diff · `pyproject.toml` diff · `frontend/package.json` diff · config grep | 0 / 0 / 0 / no new config knob |
| L11 | **Every gate leg ran with a declared tier, D1/D2 split, slicing proof, pack invocation and mutation table** | the evidence pack | all legs present; zero bare-pytest invocations; counts re-derived, never remembered |

---

## Provenance

**Inputs — canonical, read-only, unmodified by this lane:**

| Source | Path | Role |
|---|---|---|
| **THE binding design** | `.agents/shared/planning/metrics-agent-tools/technical-analysis.md` (1757 L — pointer-amended only: SEVEN inline fold pointers at `:224/:313/:315/:459/:1471/:1473/:1581`; NO content rewrites) | the full tool-surface design (§A UX), the scope-split design (§B), the watch outline (§C), the decisions (§D), the escalation (§E). Folded VERBATIM into `decisions.md`. Not redesigned. |
| Research — surface | `.agents/shared/planning/metrics-agent-tools/research-metrics-surface.md` (715 L) | contract clauses, provider seam, auth at the door, D6 verbatim, inventories, freshness |
| Research — conventions | `.agents/shared/planning/metrics-agent-tools/research-tool-conventions.md` (992 L) | privileged-category anatomy, pin ceremony (16 sites / 10 files), allow-list mechanics, alert census |
| Research — folds | `.agents/shared/planning/metrics-agent-tools/research-folds.md` (499 L) | fold (a) FE detail, fold (b) unresolvability, verification conventions, the exclusion table, the lane-#1 plan-set shape |
| **Architect rulings source (READ-ONLY)** | `.agents/shared/planning/metrics-agent-tools/architecture-recommendation.md` (139 L) | the SOUND-WITH-AMENDMENTS verdict + the consolidated fold-map + RULING-1..4 + risk deltas — the authority cited by every Am-token in `decisions.md` Part 5 and §E/§F of the fold ledger |
| Upstream lane plan set | `.agents/shared/planning/central-metrics-v1/` (esp. `decisions.md` §D4 `:370-377` + **§D6** `:641-658`, `fold-ledger.md:119` DEF-3, `phase5-plan.md`/`phase6-plan.md` shape) | the named commitment this lane executes + the house plan-set shape mirrored here |

**Plan-file manifest (this plan set):**

| File | Purpose |
|---|---|
| `plan-overview.md` | this document — objective, adjudications, scope fences + out-list, phase map + grouping rationale, coupling, risk register, success criteria, provenance, manifest |
| `phase1-plan.md` | Seam enablement: additive Protocol amendment, `read_numeric_fact` promotion, manager mount, lifespan wiring, two-fake cost, seam pin pack (Tier 2) |
| `phase2-plan.md` | Category `metrics-read` + the three tools + registry edits + factory wiring + allow entries + tools_note sections + the SAME-COMMIT pin ceremony + registration pack (Tier 1) |
| `phase3-plan.md` | `metrics:read` scope split + manifest pins + route-count non-movement + auth-matrix extension + E-1 ADJUDICATED PROCEED — contract-prose amendment primary (3.6b, same-commit, guardrails (a)-(d)), debt-record path as guardrail-(d) fallback (Tier 1) |
| `phase4-plan.md` | FE `registry_read_ok` mirror: optional member, degraded surface, fixtures, spec legs, e2e leg, type-check/build legs (Tier 1) |
| `phase5-plan.md` | Verification + docs: PACKS.md registration, mutation matrix, tool-ladder auth pack, persona acceptance tests, shell packs, docs-surface verification, evidence pack (Tier 0) |
| `phase6-plan.md` | Close-out: watch/alert outline doc, fold-ledger backfill protocol, stage-prep checklist, consistency sweep (Tier 0) |
| `architecture-recommendation.md` | the architect enrichment — BINDING INPUT, untouched (SOUND-WITH-AMENDMENTS; the fold-map + RULING-1..4 that `decisions.md` Part 5 and fold-ledger §E/§F fold) |
| `decisions.md` | the decisions record — D-UX-1..15, D-SCOPE-1..7, D-WATCH-1..4 folded VERBATIM from the design (cited by section), PLUS planner adjudications P-1..P-7 |
| `fold-ledger.md` | the fold ledger — §A IN folds (with provenance + fold site), §B OUT exclusions (with reasons), §C the provenance-gap protocol (P-2), §D recorded deviations, §G the implementation-tidier backfill placeholder |
| `research-findings.md` | the research index — pointers to the three research files with one-paragraph summaries (no content duplication) |
| `technical-analysis.md` | the design — BINDING INPUT, pointer-amended only (seven inline fold pointers; no content rewrites) |
| `research-metrics-surface.md` / `research-tool-conventions.md` / `research-folds.md` | the three research reports — INPUTS, untouched |

*(Files the IMPLEMENTATION lane creates later — not planning deliverables:* `daemon/tools/metrics_read.py`, `tests/unit/test_metrics_read_registration.py`, `tests/unit/metrics/test_metrics_query_protocol_consumer.py`, `test/packs/metrics_read_*.sh`, the mutation harness, `docs/architecture/metrics-watch-outline.md`, the evidence pack, `stage-prep-checklist.md`.*)*

---

## Disclosures

| # | Disclosure (council A8) |
|---|---|
| D-A8 | **The agent-tool door closes on the CATEGORY allow-list + the in-body `agent_id` gate — NOT the HTTP auth posture.** The three tools are in-process and never carry 401/403/503 semantics; the auth posture (fail-closed `503 AUTH_NOT_CONFIGURED`, `401`, `403 SCOPE_INSUFFICIENT`) applies to the HTTP door only (D-SCOPE-2/D-SCOPE-5). Recorded in phase5 task 5.6. This is a disclosure, not a change. |

---

## Open questions

**None blocking.** Every question the research raised is adjudicated, and the one previously-gated item is now RESOLVED: the D6/DEF-3 ruling (land the retrofit — P-1, B.5), the FE mirror shape (P-3), the audit posture (P-5), the holder set (R4 — `{maintenancer, devops, wanderer}`), the reach mechanism (D-UX-12), the cursor posture (D-UX-5), the watch lane (D-WATCH-1..4 + P-7), the tidier Mediums (P-2), and the contract prose — **E-1 ADJUDICATED PROCEED by the lane owner, 2026-11-03** (recorded verbatim in `decisions.md` Part 4 as "P-1 / E-1 — ADJUDICATED"): task 3.6b (the description-only amendment) is PRIMARY and always-executed, same-commit with the re-point under guardrails (a)-(d); task 3.6a (debt record) is the fallback reachable only via guardrail (d) (a substantive council/approver objection → lane-owner re-adjudication). No open planning question remains.
