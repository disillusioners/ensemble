# Architecture Recommendation — metrics-agent-tools

**Status:** BINDING ENRICHMENT — verdicts + foldable amendments for the planner. E-1 (P-1) is NOT re-litigated.
**Date:** 2026-11-03 (UTC)
**Mode:** Standard Design — multi-dimensional plan adjudication (3 parallel review lenses, one skill each). Not competitive fan-out: the design (`technical-analysis.md`) is binding input.
**Tree:** worktree `/home/nea/ensemble-src-wt-metrics-tools` @ `feature/metrics-agent-tools` (base `03a2297c3`). Every cite below is worktree-relative and was verified against this tree by the lens workers.

**Inputs (all first-line skill-confirmed, no skill-bank misses):**
- **Lens A** `security-design` — instance `da6e608c` (architect-worker-metrics-auth) — Focus 1 + 3
- **Lens B** `data-flow-design` — instance `8eae7f22` (architect-worker-metrics-flow) — Focus 2 + 4 + 6
- **Lens C** `resilience-design` — instance `1bfb4659` (architect-worker-metrics-risk) — Focus 8 + 5 + 7

---

## 0. Executive verdict

**SOUND-WITH-AMENDMENTS — every focus area. Zero blocking escalations for the leader.**

The single highest-severity open question at dispatch — *do the planned tools introduce sync provider reads on the event loop (the v0.27.14 defect class)?* — is **resolved NEGATIVE with proof**: the provider read path is in-memory end-to-end (in-process registry → lock-free published snapshots → per-ring-`threading.Lock` rings; **no psycopg/persistence import on the provider path**; all DB work lives in the sampler daemon thread) [C: `provider.py:26-43,152-170`; `checkpoint_metrics.py:1057`; `ring.py:108-113,340-342`; `sampler.py:66,117-124`]. What remains in that area is plan **silence** on tool-body placement (C-1) and an **output-budget enforcement gap** (merged B-5+C-2) — both foldable, neither blocking.

Every core authorization and scope claim the plan makes was **verified true** (Focus 1, Focus 3 evidence tables, Lens A). The defects found are: one mis-cited enforcement mechanism (A-1), an incomplete pin inventory with one deterministic RED and one false acceptance criterion (A-5, A-6, A-7), one internal contradiction that makes a ladder token unreachable (C-3), one example string that would RED a Phase-5 pin if copied (B-12), one success criterion only half-met as written (B-15), one budget-enforcement gap (B-5+C-2), one freshness-semantics root cause with three landing sites (B-8+C-5+C-10), and one forward-coupling seam that must be named now for item #3 (A-8).

**Must-fold-before-gate** (deterministic reds / false criteria / contradictions): **A-5, A-7, B-5+C-2, B-12, B-15(criterion), C-3, C-6, C-11**. Everything else is fold-for-quality.

---

## 1. Focus-area verdicts

| # | Focus area | Verdict | Dominant verified evidence | Amendments |
|---|---|---|---|---|
| 1 | Provider-seam authorization | **SOUND-WITH-AMENDMENTS** | Seam carries zero authz (`protocol.py:22-52`); literal-name allow bypass literally true (`instance.py:561-568`); in-body `agent_id` gate is the only unbypassable layer, server-resolved so custom agents cannot shadow the frozenset (`instance_lifecycle.py:1899`, `registry.py:880-910`); 2-layer composition **identical to the `wedge_capture`/`instance_read` read-precedents** (`_tool_registry.py:529-566`; `wedge_capture.py:84-118`; `instance_read.py:124-128,199-212`); `notify_user`'s 3rd layer is a kill-switch for side-effect blast radius, correctly rejected for read-only | A-1..A-4 |
| 2 | Protocol amendment (Phase 1) | **SOUND-WITH-AMENDMENTS** | GAP-1/2 is a **repair, not speculation** — the envelope already calls `provider.history(now=…, max_series=…)` with kwargs the Protocol does not declare (`envelope.py:203-210`); purely additive; no consumer re-typed (router types provider as `Any`, `routers/metrics.py:219-233`); zero wire change (`constants.py:25` untouched); sequencing correct (P1→P2 hard order); two-fake cost verified by grep | B-1 (+RULING-3) |
| 3 | `metrics:read` scope split | **SOUND-WITH-AMENDMENTS** | Any-of is the house lattice **by construction** (`decorators.py:94-105`); operator lane auto-carries the mint (`scopes.py:111`), normalize rejects unknown scopes ⇒ mint-first ordering is load-bearing (`scopes.py:119-141`); auth-OFF is a byte-identical no-op (503 from the posture gate before any scope check, `decorators.py:110-111`, `routers/metrics.py:121-137,167`); the D6 over-grant closure is real — `maintenance:read` today authorizes the **destructive** checkpoint-cleanup execute (`routers/maintenance.py:214,255,277-300`); manifest family cage green both directions (`test_router_scope_manifest.py:202-258`); route census 111 verified (`:409`) | A-5..A-10 (A-8 substantive) |
| 4 | Tool output shape | **SOUND-WITH-AMENDMENTS** | Defaults lean on verified provider constants (`constants.py:33-92`); envelope's effective ceiling 12,240 = the D-UX-4 carve-out (`envelope.py:244-249`); `narrow_hint` concrete; `registry_read_ok` exists BE-side with default-safe emit (`provider.py:98-107`, `envelope.py:70`), absent FE-side (0 hits `frontend/src/`) | B-2..B-6, B-8..B-11, B-13, B-14 + merged C-2, C-5 |
| 5 | Pin ceremony (16/10, one commit) | **SOUND-WITH-AMENDMENTS** | Census **verified exact against the tree** — all 16 sites exist at cited lines; registry = exactly 16 entries today ⇒ 16→17 correct; precedent arithmetic matches (notify-user corrected to 16/10 by tidier; nest-ops 15-member both-contracts); enforcement-point coverage complete; no seal re-pin needed (none of the 35 `SEALED_SHA_BASELINES` rows is among the 6 touched `agents/` files) | C-7, C-8, C-9 |
| 6 | FE `registry_read_ok` mirror (P-3) | **SOUND-WITH-AMENDMENTS** | P-3 **sound and closes** the 3-vs-4 mismatch: BE emits 4 (`envelope.py:66-71`), FE type declares 3 and never reads `payload.activation`; optional member + `=== false` mirrors BE default-safe `getattr(...,True)`; no versioning hazard; `:15` pin byte-preserved (`metrics.component.spec.ts:47-59,196-201`); no other spec pin conflicts | B-15 (+RULING-2) |
| 7 | Watch/alert outline (§C/Phase 6) | **SOUND-WITH-AMENDMENTS** | Genuinely design-only — hard "plans NOTHING" bound stated; `config.yaml`/`config.py` expected EMPTY delta; **no shipped mechanism smuggled** (the §C.3 knobs phrase describes existing `SchedulingConfig` fields + a future lane's choice) | C-10, C-11 |
| 8 | Risk register + event-loop | **SOUND-WITH-AMENDMENTS** | 🔴-candidate **resolved negative** (see §0); tool-lane execution path traced (`graph.py:13077-13084` → `ToolNode._arun_one` → `StructuredTool._arun`; async body runs ON the loop, sync body → default executor, `langchain_core/tools/base.py:788-801`); concurrency safe (per-ring locks, copies under lock, no torn reads); mount shared-with-door intended; register R1-R12 all REAL, priorities need re-rank (C-6) | C-1..C-6 |

---

## 2. Consolidated amendment fold-map

IDs preserve the lens reports' numbering for traceability. Merged clusters marked.

### Cluster M-1 — output budget enforcement (merge of **B-5 + C-2**) — 🟡 must fold
The tool path inherits **none** of the door's output bounds: `MAX_TOTAL_POINTS` is enforced in the envelope layer only (`envelope.py:213,231-250`), `RESPONSE_BYTE_CEILING` in the router (`routers/metrics.py:338-343,391-405`), `max_series=500` in the envelope (`envelope.py:163`) — the tool calls `provider.history` directly and bypasses all three (provider enforces only `max_series`, `provider.py:354,391-392`). Compounding: `compare=true` is the **default** (`technical-analysis.md:385-392`) and issues two legs ⇒ provider work at defaults = 2,640 pts (**22%**, not the plan's 11%) and 24,480 pts at ceilings; and the tool ceiling 51×240 = **12,240 is 240 points ABOVE the contract's declared `MAX_TOTAL_POINTS` = 12,000** (`technical-analysis.md:455` prints both without flagging); ≈300 KB ≈ **70k tokens** in one agent turn at ceiling with no byte/token guard (defaults 1,320 pts ≈ 33 KB ≈ 9k tokens — material for a 7-call patrol).
**Fold:** (a) clamp the returned leg so worst case ≤ `MAX_TOTAL_POINTS` (e.g. `max_points ≤ MAX_TOTAL_POINTS // (top_n+1)`); (b) declare the compare leg's transient work separately (it returns only scalars — provider work, not agent-context tokens); (c) add a tool-local byte ceiling with the same trim + six-member bound block the door uses; (d) state the default-call agent-context cost in `CATEGORY_DOC`.

### Cluster M-2 — freshness/staleness semantics (merge of **B-8 + C-5 + C-10**) — 🟡 must fold (one root cause, three landing sites)
Root cause: ring freshness signals cannot distinguish **empty-ring** from **fresh**. After a restart the ring is in-RAM-empty, `last_sample_age_s` returns **0.0** (`provider.py:397-404` — `newest is None → 0.0`), derived `stale` is **false** (`constants.py:92` rule), and `availability` can still read **"live"** (`availability.py:76-92` has no empty-ring reason) — the agent sees "fresh but empty" for the ~15 min ref-fill window (15 s cadence, `MIN_DEPTH=60`, `constants.py:37`). Separately, history rows carry **no per-series** freshness (top-level `last_sample_age_s` is ring-global, `provider.py:397-404`; snapshot rows have per-metric `last_publish_age_s`, `provider.py:297-305` — history rows have none), which persona (a) explicitly needs for stale-vs-absent.
**Fold:** (i) tool payload: per-series `last_sample_age_s` (age of that series' newest point vs anchor) and/or `points_span_s` on history rows [B-8]; (ii) `read_status`: an explicit ring-state signal (e.g. `trend_empty_since_restart` or a distinct caveat) [C-5]; (iii) outline §C.2/§C.4: replace the wrong "Gap: none" cell — the recommended `last_sample_age_s > 3×interval` trigger is **silently inert** for the whole post-restart window, exactly the wedged-sampler incident family it claims to cover [C-10].

### Cluster M-3 — mount/unavailable contradiction (**C-3**, absorbing Lens B's UNVERIFIED mount trace) — 🟡 must fold, resolved by RULING-1
`phase2-plan.md:29` (task 2.6 C2) returns `[]` from the factory when `manager.metrics_query_provider is None` (house precedent `instance.py:2667-2699`), but `decisions.md:123`, `technical-analysis.md:1409,1427` and `phase5-plan.md:16` (task 5.3) require a typed `metrics-unavailable` refusal for that exact condition. With tools absent the body never runs ⇒ **the token is unreachable**; on degraded boot (`api.py:2540-2547`) the metrics surface vanishes silently from every holder's tool list. See RULING-1.

### Remaining amendments (unmerged, by focus)

**Focus 1**
- **A-1 (🟢 citation hygiene)** — D-SCOPE-2's rationale cites `_tool_registry.py:559-560` as the bypass mechanism; that range is a **comment**. Replace with the enforcement cites `instance.py:550-560` (default-deny strip) + `:561-568` (literal-name fall-through).
- **A-2 (🟡 dead-tool hazard)** — task 2.6 must state the factory call **passes `agent_id=agent_id`** (the `create_instance_tools` parameter, `instance.py:3017`), mirroring `instance.py:6542-6549,6589-6596` whose comments warn omission ⇒ body gate denies every call (fail-closed but a dead tool). Add a registration-pack leg asserting the **threaded** value, not just the signature.
- **A-3 (🟡 two-set divergence)** — category-gate holder set and body-gate allow-set are independent spellings of the audience; `wedge_capture` pins each separately and never asserts equality (`test_wedge_capture_registration.py:234-246` vs `:261-277`). Add one equality leg: `{agents whose meta.allow holds "metrics-read"} == METRICS_AUTHORIZED_AGENTS`.
- **A-4 (🟢 anti-relitigation)** — fold the precedent-composition rationale (read = 2 layers; write/act = +kill-switch/ledger; exists at `technical-analysis.md:1434`) into `decisions.md` D-SCOPE-2 where reviewers will look.

**Focus 2**
- **B-1 (🟡 size)** — no v1 consumer is identified for GAP-4/5 (`listener_enabled`, `census_enabled`); the seam already ships `activation_block(provider)` (`envelope.py:56-71`, exported `__init__.py:108`) returning the exact wire block. Resolved by RULING-3.

**Focus 3**
- **A-5 (🟡 must fold — deterministic RED + false criterion)** — `test_metrics_query_auth_matrix.py:114-131` asserts exact set equality `scopes == {"maintenance:read"}`; after the re-point this is RED, and phase-3 task 3.5's "original rows unchanged" criterion is **false** for this row. Amend to `frozenset({"maintenance:read","metrics:read"})` and add to the enumerated pin list (absent from D-SCOPE-6's two-file list).
- **A-6 (🟡 truth-in-pins)** — `test_metrics_query_auth_matrix.py:133-137` (`test_no_new_scope_is_minted_for_the_door`) and the file docstring `:3-15` become false after the mint; rename/reword in the same commit.
- **A-7 (🟡 must fold — truth-in-docs, same file the lane edits)** — `routers/metrics.py` still says "no new scope is minted" / names only `maintenance:read` at `:32,:53,:55,:62-63,:180,:190` and in the four OpenAPI 403 descriptions `:153,:564,:609,:677`. Fold docstring + 403-description updates into task 3.2 (OpenAPI description text only; no wire-shape change).
- **A-8 (🟡 substantive — forward-declared seam for item #3)** — the Prometheus **text** route is the scraping surface (`routers/metrics.py:157-168`, "no filtering, no reshaping" ⇒ the wider surface) and KEEPS `maintenance:read` alone (D6 step 3, `technical-analysis.md:1363`). A Grafana datasource consumes the text surface, not the JSON door ⇒ an item-#3 token with `metrics:read` alone is 403 there, forcing either `maintenance:read` (re-opening the exact destructive-execute over-grant D6 closes, `routers/maintenance.py:214,255`) or a **second** amendment of the same manifest family pin (`test_router_scope_manifest.py:169`) **plus a reversal** of phase-3 task 3.5 row (ii). **Fold:** forward-declare the seam — one sentence in the E-1 amendment (if it fits guardrail (a)'s 4 sites) or as an explicit item-#3 debt entry in `docs/architecture/metrics-query-api.md:280-292`; label task 3.5 row (ii) a **reversal pin**; name `:169` as the second-amendment site. This COMPOSES with E-1's guardrails — it is not an objection to the ruling.
- **A-9 (🟡 mutation-pack coupling)** — `test/packs/metrics_fe_mutation_matrix_test.py:212-231` (FIX2-A5) embeds the FE 403 copy literal naming only `maintenance:read`; if FE copy is updated (per A-7), the `old=` block stops matching and the mutation silently no-ops. Update the pack in the same commit, or leave copy unchanged and record the drift.
- **A-10 (🟢 conditional pin)** — `test_metrics_query_scope_cage.py:159-166` asserts the literal decorator substring for the TEXT route; stays green only if the text route's decorator literal is preserved verbatim (task 3.2's second constant for the query routes is the right split). State it in task 3.2's acceptance.

**Focus 4** (beyond M-1/M-2)
- **B-2 (🟡 must fold — mis-cited guard)** — D-UX-4/2.4 cite `provider.py:346-349` as the selector ceiling, but that guard caps **`names` only**; the label-aware `series` list is uncapped provider-side (`provider.py:342-344`); the combined cap lives in the **router** (`routers/metrics.py:719`). The tool bypasses the router ⇒ its own refusal is the only enforcement. **Fold:** refuse on `len(set(names)) + len(series) > MAX_NAMES` (mirror the router formula exactly); correct the citation.
- **B-6 (🟡 false ordering promise)** — A.3 promises snapshot rows "ascending by (family, name, label key)" (`technical-analysis.md:305-310`) but the provider returns **registration order** (`provider.py:289` → `checkpoint_metrics.py:437-441`); with the `top_n` cut applied after the provider returns, **which** rows survive is arbitrary. **Fold:** tool sorts before the cut (tool-side, zero provider change; also makes `dropped_count` meaningful) — or drop the doc promise.
- **B-9 (🟢 trailing-only windows)** — no way to target a window ending in the past (post-incident review); ring holds 2 h so the capability is nearly free. **Fold:** `end_offset_s: int = 0` (window END shifted back from now; clamp `[0, WINDOW_SECONDS − window_s]`); compare leg anchors at `now − end_offset_s − window_s`. Preserves the no-cursor doctrine (anchor stays relative).
- **B-10 (🟢 multi-family snapshot — recommended, deviation must be pinned)** — `provider.snapshot(family=…)` takes ONE family (`provider.py:281-292`); persona (b)'s decision set ⇒ 5 snapshot calls + 1 history + 1 catalog = **7 round trips** per patrol. **Fold:** `families: list[str] | None` implemented tool-side over ONE provider call (`family=None` → local filter → sort → cut), the same tool-side pattern already used for `top_n`/`only_with_data`. Deviates from A.3's "exactly the provider's two kwargs" (`technical-analysis.md:313`) — say so and pin it. (Planner may defer; then record the 7-round-trip cost in `CATEGORY_DOC`.)
- **B-11 (🟢 non-goal declaration)** — no anomaly-first ordering or cross-family top-N exists; the only ordering is deterministic/registry (B-6). Declare it a non-goal in `CATEGORY_DOC` + docstrings (the anomaly signal is `metrics_history`'s `delta_change`) so no agent assumes a ranking that isn't there.
- **B-13 (🟢 wording)** — the closed availability reason set has no "dark-but-publishing" member; the shipped caveat must say the series below are real (A.9's intent) — no cause attribution.
- **B-14 (🟢 cosmetic)** — "quintet" is six members (`technical-analysis.md:224` vs the six-row table `:488-495`; `decisions.md:40` lists six). Rename "the six-member bound block".

**Focus 5**
- **C-7 (🟢)** — task 2.8's header says 16 sites but enumerates 15 (5+5+5); the 16th (ari trailing pin, `test_board_registration.py:249-287`) needs **no change** (metrics-read not granted to ari). State "15 edit sites + 1 verify-only".
- **C-8 (🟡 must fold)** — the seal/prompt-artifact coupling has **no verification command**: verified no `SEALED_SHA_BASELINES` row (`tests/test_chart_image_delivery_e2e.py:1425`) is among the 6 touched `agents/` files ⇒ no re-pin needed, and the 161-file baseline check (`scripts/generate_prompt_artifact_baseline.py --check`) is only recorded, never run as a gate leg. Add both as explicit verification commands (phase-5 gate 5.8 / stage-prep 6.3).
- **C-9 (🟢)** — make `phase5` 5.7's grep re-derivation a **post-merge** artifact (a merge conflict inside any of the 10 pin files is the one way the one-commit cluster splits).

**Focus 6**
- **B-15 (🟡 must fold — resolved by RULING-2)** — the fold covers only the shell's catalog load; the Overview tab poll (`overview-tab.component.ts:284-296`, renders "No series published yet.") and Charts tab poll (`charts-tab.component.ts:301-311`, "No samples in the window.") have the **identical** blind spot — "no data" where the truth is "unknown". Neither reads `payload.activation`. As written, phase-4's success criterion ("Failure is no longer indistinguishable from empty") is **half met**.

**Focus 7**
- **C-10** — folded into M-2(iii).
- **C-11 (🟡 must fold)** — D-WATCH-4's binding "must be stated explicitly in the shipped docs" (`decisions.md:171`, `technical-analysis.md:1693`) has **no landing site**: phase-5 5.6 verifies the docs chain but never requires the asymmetry sentence; phase-6 6.1 lands it in a repo doc **not in the release payload**. **Fold:** add the sentence to `CATEGORY_DOC` (loader-rendered) + the three `tools_note.md` sections + extend the 5.6 fixture to assert it — or downgrade the clause and record that.

**Focus 8** (beyond M-1/M-3)
- **C-1 (🟡 must fold — decision silence, resolved by RULING-4)** — the plan never chooses sync vs async for the three tool bodies (grep over the 15 planning files: no tool-shape decision). House template = `async def` (`research-tool-conventions.md:66-71`; `wedge_capture.py:190`, `instance_read.py:592`, `notify_user.py:505`) ⇒ default landing = body build ON the main loop, while the door deliberately off-loads (`routers/metrics.py:441-451` → `db_offload`). Bounded either way (~3.7 ms worst case at 12,240 pts, house AM-12 figure `constants.py:51-57,70-78`) — this is silence, not a proven defect — but the choice must be made and pinned.
- **C-4 (🟢)** — shared-instance `registry_read_ok` is one mutable bool flipped by every reader (`provider.py:156,159`); benign race, but the tool's `read_status` presents it as *that call's* fact. Make it per-call or state the racy semantics in the `read_status` doctrine and pin it. (Composes with RULING-3: per-call read via `activation_block` at call time.)
- **C-6 (🟡 must fold)** — the register (`plan-overview.md:101-116`) has **no entry** for loop interaction, output/context blowup, or missing-mount behaviour, while carrying a High process risk (R7) and a Low-Medium one (R10). Add the three (now materially de-risked by this review: M-1, C-1, M-3) and re-rank.

---

## 3. Architect rulings (under-determined points — I rule, planner folds)

- **RULING-1 (C-3): Option A — tools stay present; provider resolved at CALL time; typed `metrics-unavailable` refusal.** Grounds: (1) the plan's own doctrine weaves the ladder token through D-SCOPE-4, task 5.3's pin, and the completion test ("every tool call is authorized by the in-body gate BEFORE the provider is touched" presupposes the body runs); (2) the stronger in-house precedent for a *substrate-degraded* tool is `notify_user`'s kill-switch shape — the tool remains present and refuses loudly at call time (`notify_user.py:709`), rather than vanishing; for a **diagnosis** surface, silent absence is the worst failure mode (the diagnosing agent doesn't know what it's missing); (3) Option B (delete the token) touches more pinned surfaces than it spares. Constraints: the factory still gates on module + manager handle existence (construction), the **body** reads `manager.metrics_query_provider` per call, a registration-pack leg pins the degraded-boot shape (tools present + refusal), and task 1.6(ii)'s `manager.metrics_query_provider IS app.state.metrics_query` pin stands. Naming: prefer dropping `_if_available` from the factory name (same naming-debt class as the v0.27.8 deferred `create_notify_user_tools_if_available` rename).
- **RULING-2 (B-15): amend the phase-4 success criterion to scope it to the catalog/shell surface, and record the tab-poll residual as routed debt** (joining the existing v0.28.6 `fe_registry_read_ok_mirror` followup family). Rationale: the tab polls are a pre-existing v0.28.6 FE blind spot, not created by this lane; P-3's binding shape is the shell mirror. In-lane extension remains the planner's option with FE cost — but the criterion as written is false either way and must be corrected now.
- **RULING-3 (B-1): package (ii) — the tool consumes `activation_block(provider)` and derives `read_status.registry_read_ok` from that block; the Protocol amendment trims to GAP-1/GAP-2 (+GAP-3 only if the pack wants a typed surface for the pin).** Rationale: GAP-4/5 have no v1 consumer ("a Protocol no consumer references guarantees nothing"); consuming the shipped builder kills the block-drift risk (the wire's `query_api` literal is hardcoded `envelope.py:69`) and makes C-4's per-call read natural. Pin the construction path in `phase2-plan.md:25-27`; make `phase1-plan.md:23` + the completeness pin consistent with the trimmed set.
- **RULING-4 (C-1): keep `async def` (house template) AND off-load the body build via `db_offload.offload`, mirroring `routers/metrics.py:441-451`.** Rationale: the door already established the pattern for exactly this payload; async-native keeps the tool out of the default executor (the pool the v0.27.14 lane found saturating); the ~3.7 ms figure says the cost is bounded either way, so the ruling optimizes for pattern-consistency and future budget growth, not present need. Pin the callable shape in the registration pack (a leg asserting the coroutine + offload composition).

---

## 4. Risk register deltas (C-6)

Add (post-review severities):
- **R13 (🟡→low residual): tool output/context blowup** — M-1; residual after fold: bounded by clamp + byte ceiling.
- **R14 (🟢): loop interaction of tool bodies** — resolved negative for DB (§0); placement decision pinned by RULING-4; worst-case on-loop build ~3.7 ms even unoffloaded.
- **R15 (🟡): missing-mount behaviour** — resolved by RULING-1 (loud refusal, pinned); pre-ruling this was a silent-vanish defect.

Re-rank: R7 (worktree discipline, process) stays High for *process* but no longer outranks the (now-closed) runtime items; R1-R6/R8-R12 all verified REAL with cited evidence — no changes to their content.

---

## 5. Decisions pending (planner folds; none require the user)

1. **C-3 shape** — ruled (RULING-1, Option A); planner folds mechanically. Objecting requires new evidence per the same standard as E-1 guardrail (d).
2. **B-15 scope** — ruled (RULING-2, criterion amendment + routed residual); in-lane tab extension is a legitimate planner override with FE cost disclosed.
3. **B-10 multi-family** — recommended fold with pinned deviation; deferring is acceptable if the 7-round-trip patrol cost is documented in `CATEGORY_DOC`.

## 6. Open questions / implementation-time pins

- **Mount handle resolution** (Lens B UNVERIFIED): `daemon/manager.py` contains no `metrics_query` reference today (expected — task 1.4 adds it); the lifespan handle → live-manager reachability must be pinned at implementation (task 1.6(ii) partially covers; add an explicit leg).
- **A-10 decorator literal**: whether the implementer's form keeps `test_metrics_query_scope_cage.py:159-166` green is implementation-time; acceptance must state the constraint.
- **C-1 cost figure** (~3.6-3.7 ms) is the house AM-12 figure, not a local measurement of the tool's JSON build — acceptable for a bounded decision; re-measure only if the pack wants a latency leg.
- Custom-agent bare-id registration path (Lens A UNVERIFIED tail): the body-gate shadowing safety rests on the `custom:` namespace guard (`registry.py:880-910`); a full registration-path trace was not done — low risk, note for the registration pack.

## 7. Gaps

None. Fan-in 3/3 complete; all three lenses first-line skill-confirmed (`security-design`, `data-flow-design`, `resilience-design` — the 2026-10-11 skill-bank gap note is stale); no re-dispatches; no DEGRADED markers. Unverified items are enumerated in §6, none load-bearing for the verdicts.
